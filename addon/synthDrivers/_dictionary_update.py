"""Dictionary Update: refresh local Pronunciation Dictionaries from a Dictionary Source.

update_dictionaries owns the whole operation: download, decoding, merge rules, duplicate handling,
writing and temporary-file cleanup. It blocks on the network and the disk, so callers must run it
outside NVDA's UI thread. It has no NVDA or wx dependencies.
"""

import enum
import logging
import os
import re
import tempfile
import unicodedata
import urllib.request
import zipfile
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Longest wait for any single connect or read, so a stalled server ends in an error instead of a hang.
NETWORK_TIMEOUT_SECONDS = 30
# Dictionary Sources are a few hundred kilobytes. Anything this large is not a Dictionary Source.
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
# The Eloquence Engine reads western Pronunciation Dictionaries in the Windows ANSI code page.
DICTIONARY_ENCODING = "cp1252"


class DictionaryUpdateError(Exception):
	"""The Dictionary Update could not start, so no Pronunciation Dictionary was touched."""


class UpdateStage(enum.Enum):
	DOWNLOADING = "downloading"
	MERGING = "merging"


class FileStatus(enum.Enum):
	CREATED = "created"
	UPDATED = "updated"
	UNCHANGED = "unchanged"
	FAILED = "failed"


@dataclass(frozen=True)
class FileOutcome:
	filename: str
	status: FileStatus
	entries_added: int = 0
	error: str = ""


@dataclass(frozen=True)
class DictionaryUpdateResult:
	"""What happened to each Pronunciation Dictionary offered by the Dictionary Source.

	When cancelled is True, files lists only the dictionaries finished before the user cancelled.
	"""

	files: tuple[FileOutcome, ...] = ()
	cancelled: bool = False

	@property
	def entries_added(self):
		return sum(outcome.entries_added for outcome in self.files)

	@property
	def changed(self):
		return tuple(o for o in self.files if o.status in (FileStatus.CREATED, FileStatus.UPDATED))

	@property
	def failed(self):
		return tuple(o for o in self.files if o.status is FileStatus.FAILED)


def update_dictionaries(source_url, dictionary_dir, is_cancelled=lambda: False, report_progress=None):
	"""Merge the Pronunciation Dictionaries from source_url into dictionary_dir.

	Local entries always win: the merge only appends source entries whose key is not present locally.
	Each dictionary is replaced atomically, so cancelling or failing part way never leaves a half-written file.
	report_progress(UpdateStage) and is_cancelled() are called on the calling thread.
	Raises DictionaryUpdateError or OSError when the Dictionary Source cannot be fetched or read.
	"""
	report = report_progress or (lambda stage: None)
	with tempfile.TemporaryDirectory(prefix="eloquence-dictionaries-") as work_dir:
		archive_path = os.path.join(work_dir, "source.zip")
		report(UpdateStage.DOWNLOADING)
		if not _download(source_url + "/archive/master.zip", archive_path, is_cancelled):
			return DictionaryUpdateResult(cancelled=True)
		report(UpdateStage.MERGING)
		try:
			with zipfile.ZipFile(archive_path) as archive:
				return _merge_archive(archive, dictionary_dir, is_cancelled)
		except zipfile.BadZipFile as e:
			raise DictionaryUpdateError(f"The Dictionary Source did not return a valid archive: {e}") from e


def _download(url, dest_path, is_cancelled):
	"""Stream url to dest_path. Returns False if the user cancelled."""
	request = urllib.request.Request(url, headers={"User-Agent": "NVDA-Eloquence-Updater"})
	with (
		urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT_SECONDS) as response,
		open(dest_path, "wb") as f,
	):
		downloaded = 0
		while True:
			if is_cancelled():
				return False
			block = response.read(8192)
			if not block:
				return True
			downloaded += len(block)
			if downloaded > MAX_ARCHIVE_BYTES:
				raise DictionaryUpdateError("The Dictionary Source archive is too large.")
			f.write(block)


def _merge_archive(archive, dictionary_dir, is_cancelled):
	members = _select_dictionary_members(archive)
	if not members:
		raise DictionaryUpdateError("The Dictionary Source contains no Pronunciation Dictionaries.")
	os.makedirs(dictionary_dir, exist_ok=True)
	local_names = {name.lower(): name for name in os.listdir(dictionary_dir)}
	outcomes = []
	for key, member in sorted(members.items()):
		if is_cancelled():
			return DictionaryUpdateResult(tuple(outcomes), cancelled=True)
		# Reuse the local file's spelling so ENUmain.dic from one source merges into enumain.dic from another.
		filename = local_names.get(key, os.path.basename(member.filename))
		try:
			outcome = _merge_file(archive.read(member), os.path.join(dictionary_dir, filename))
		except Exception as e:
			log.error(f"Failed to update Pronunciation Dictionary {filename}: {e}")
			outcome = FileOutcome(filename, FileStatus.FAILED, error=str(e))
		outcomes.append(outcome)
	return DictionaryUpdateResult(tuple(outcomes))


def _select_dictionary_members(archive):
	"""Map lower-case dictionary file name to the archive member that provides it.

	A Dictionary Source may carry the same file name more than once, for example a variant for another
	engine version in a subfolder. The copy closest to the archive root is the one the source means
	for general use, so it wins and the others are ignored.
	"""
	members = {}
	for member in archive.infolist():
		if member.is_dir() or not member.filename.lower().endswith(".dic"):
			continue
		key = os.path.basename(member.filename).lower()
		rank = (member.filename.count("/"), member.filename)
		if key not in members or rank < members[key][0]:
			members[key] = (rank, member)
	return {key: member for key, (rank, member) in members.items()}


def _merge_file(source_data, dest_path):
	filename = os.path.basename(dest_path)
	if b"\x00" in source_data:
		raise ValueError("The source file is not a text dictionary.")
	case_sensitive = _is_case_sensitive(filename)
	local_data = b""
	is_new = not os.path.exists(dest_path)
	if not is_new:
		with open(dest_path, "rb") as f:
			local_data = f.read()

	seen_keys = set()
	for entry in _parse_entries(_decode(local_data)):
		seen_keys.add(_merge_key(entry[0], case_sensitive))

	additions = []
	for key, translation in _parse_entries(_decode(source_data)):
		key, translation = _to_engine_text(key), _to_engine_text(translation)
		if key is None or translation is None:
			continue
		merge_key = _merge_key(key, case_sensitive)
		if merge_key in seen_keys:
			continue
		seen_keys.add(merge_key)
		additions.append(f"{key}\t{translation}\r\n".encode(DICTIONARY_ENCODING))

	if not additions:
		return FileOutcome(filename, FileStatus.UNCHANGED)

	# Local bytes are kept exactly as they are; only the new entries are encoded here.
	if local_data and not local_data.endswith((b"\n", b"\r")):
		local_data += b"\r\n"
	_write_atomically(dest_path, local_data + b"".join(additions))
	status = FileStatus.CREATED if is_new else FileStatus.UPDATED
	return FileOutcome(filename, status, entries_added=len(additions))


def _is_case_sensitive(filename):
	"""Main and abbreviation dictionary keys are case-sensitive in the Eloquence Engine; root keys are not."""
	return not os.path.splitext(filename)[0].lower().endswith("root")


def _merge_key(key, case_sensitive):
	return key if case_sensitive else key.lower()


def _decode(data):
	"""Decode a dictionary file of unknown encoding.

	Dictionary Sources publish a mix of UTF-8 and ANSI files. Text with accented characters in cp1252 is
	almost never valid UTF-8, so trying UTF-8 first separates the two. The few bytes cp1252 leaves
	undefined become U+FFFD, which only affects the entry they sit in. Decoded local text is used for
	key comparison only and is never written back.
	"""
	try:
		return data.decode("utf-8-sig")
	except UnicodeDecodeError:
		return data.decode(DICTIONARY_ENCODING, errors="replace")


def _parse_entries(text):
	"""Yield (key, translation) for each valid line. A key is the first run of non-whitespace characters.

	Lines with no translation are not valid entries, and the engine requires a tab between the two
	parts, so entries separated by spaces come out normalised.
	"""
	for line in re.split(r"\r\n|\r|\n", text):
		parts = line.strip().split(None, 1)
		if len(parts) == 2:
			yield parts[0], parts[1]


def _to_engine_text(text):
	"""Return text the engine's code page can represent, or None if accents cannot be stripped to get there."""
	for candidate in (text, _strip_accents(text)):
		try:
			candidate.encode(DICTIONARY_ENCODING)
			return candidate
		except UnicodeEncodeError:
			continue
	return None


def _strip_accents(text):
	return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _write_atomically(dest_path, data):
	directory, filename = os.path.split(dest_path)
	# A unique name in the same directory, so the replace stays on one volume and cannot clobber a user's file.
	descriptor, temp_path = tempfile.mkstemp(dir=directory, prefix=filename + ".", suffix=".tmp")
	try:
		with os.fdopen(descriptor, "wb") as f:
			f.write(data)
		os.replace(temp_path, dest_path)
	except BaseException:
		try:
			os.remove(temp_path)
		except OSError:
			pass
		raise
