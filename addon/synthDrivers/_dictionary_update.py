"""Dictionary Update: download Dictionary Sources as Managed Dictionary Sets.

A Dictionary Source is a GitHub repository. An update downloads its default branch and stores it
under sources/ in the configuration as one set for the repository's top-level files and one for each
folder that holds dictionary files. Nothing is merged into the user's own files, so a later update
brings upstream corrections and removals with it, and switching a source off leaves no trace.

Everything here blocks on the network and the disk, so callers must run it outside NVDA's UI thread.
It has no NVDA or wx dependencies.
"""

import enum
import logging
import os
import re
import shutil
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass

from . import _dictionary_library as library
from . import _dictionary_sets as sets
from . import _dictionary_stack as stack

log = logging.getLogger(__name__)

# Longest wait for any single connect or read, so a stalled server ends in an error instead of a hang.
NETWORK_TIMEOUT_SECONDS = 30
# Dictionary Sources are a few megabytes at most. Anything this large is not a Dictionary Source.
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024


class DictionaryUpdateError(Exception):
	"""A Dictionary Source could not be fetched or read, so what was stored from it is untouched."""


class UpdateStage(enum.Enum):
	DOWNLOADING = "downloading"
	CHECKING = "checking"


class FileStatus(enum.Enum):
	CREATED = "created"
	UPDATED = "updated"
	UNCHANGED = "unchanged"
	FAILED = "failed"
	REMOVED = "removed"


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


GITHUB = "https://github.com"
_REPOSITORY = re.compile(r"(?:https?://github\.com/)?([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?")
_SET_FILE = re.compile(r"[a-z]{3}(main|root|abbr)\.dic")


@dataclass(frozen=True)
class DownloadedFile:
	filename: str
	status: FileStatus
	# Entries that may not take effect, as stack.EntryReport, for the user to review.
	reports: tuple = ()
	# For FAILED: why the file was rejected, and the line if there is one. The copy from the
	# previous update, if any, stays in use.
	error: str = ""
	error_line: int = 0


@dataclass(frozen=True)
class DownloadedSet:
	set_id: str
	name: str
	files: tuple[DownloadedFile, ...] = ()


@dataclass(frozen=True)
class SourceResult:
	"""What updating one Dictionary Source came to."""

	repository: str
	sets: tuple[DownloadedSet, ...] = ()
	cancelled: bool = False
	# An installed add-on already provides this dataset, so its copy is used and nothing was downloaded.
	provided_by_add_on: bool = False
	# Set when the source could not be fetched or read at all; what was stored before is untouched.
	error: str = ""


def parse_repository(text):
	"""Return (owner, name) for "owner/name" or a github.com URL, or None if it is neither."""
	match = _REPOSITORY.fullmatch(text.strip())
	return (match.group(1), match.group(2)) if match else None


def update_sources(
	repositories,
	sources_directory,
	installed_ids=frozenset(),
	is_cancelled=lambda: False,
	report_progress=None,
	base_url=GITHUB,
):
	"""Update each Dictionary Source in turn. One that fails does not stop the others.

	Returns a SourceResult per repository attempted; after a cancellation the rest are not started.
	"""
	results = []
	for repository in repositories:
		if is_cancelled():
			break
		try:
			result = update_source(
				repository, sources_directory, installed_ids, is_cancelled, report_progress, base_url
			)
		except (DictionaryUpdateError, OSError) as error:
			log.error("Could not update the Dictionary Source %s: %r", repository, error)
			result = SourceResult(repository, error=str(error))
		results.append(result)
		if result.cancelled:
			break
	return tuple(results)


def update_source(
	repository,
	sources_directory,
	installed_ids=frozenset(),
	is_cancelled=lambda: False,
	report_progress=None,
	base_url=GITHUB,
):
	"""Download one Dictionary Source and store it as Managed Dictionary Sets under sources_directory.

	Downloading never switches anything on. A file that has to be rejected keeps its copy from the
	previous update. report_progress(UpdateStage, repository) and is_cancelled() are called on the
	calling thread. Raises DictionaryUpdateError or OSError when the source cannot be fetched or read.
	"""
	parsed = parse_repository(repository)
	if parsed is None:
		raise DictionaryUpdateError(f"{repository!r} is not a GitHub repository.")
	owner, name = parsed
	top_level_id = stack.set_id_for_repository(owner, name)
	if top_level_id is None:
		raise DictionaryUpdateError(f"{repository} cannot be used as a Dictionary Source.")
	if top_level_id in installed_ids:
		return SourceResult(repository, provided_by_add_on=True)
	report = report_progress or (lambda stage, repository: None)
	repository_url = f"{base_url}/{owner}/{name}"
	with tempfile.TemporaryDirectory(prefix="eloquence-dictionaries-") as work_dir:
		archive_path = os.path.join(work_dir, "source.zip")
		report(UpdateStage.DOWNLOADING, repository)
		# HEAD rather than a branch name, so the default branch is used whatever it is called.
		if not _download(f"{repository_url}/archive/HEAD.zip", archive_path, is_cancelled):
			return SourceResult(repository, cancelled=True)
		report(UpdateStage.CHECKING, repository)
		try:
			with zipfile.ZipFile(archive_path) as archive:
				return _store_archive(
					archive,
					repository,
					owner,
					name,
					repository_url,
					sources_directory,
					installed_ids,
					is_cancelled,
				)
		except zipfile.BadZipFile as e:
			raise DictionaryUpdateError(f"The Dictionary Source did not return a valid archive: {e}") from e


def _store_archive(
	archive, repository, owner, name, repository_url, sources_directory, installed_ids, is_cancelled
):
	folders = _dictionary_folders(archive)
	if not folders:
		raise DictionaryUpdateError("The Dictionary Source contains no Pronunciation Dictionaries.")
	# GitHub writes the commit the archive was made from into the zip comment.
	revision = archive.comment.decode("ascii", errors="replace")
	stored = []
	for folder, members in sorted(folders.items()):
		if is_cancelled():
			return SourceResult(repository, tuple(stored), cancelled=True)
		set_id = stack.set_id_for_repository(owner, name, folder)
		if set_id in installed_ids:
			continue
		set_name = f"{name}/{folder}" if folder else name
		target = os.path.join(sources_directory, set_id)
		files = _store_set(archive, members, target, set_id, set_name, repository_url, revision)
		stored.append(DownloadedSet(set_id, set_name, files))
	return SourceResult(repository, tuple(stored))


def _dictionary_folders(archive):
	"""Return {folder: {canonical file name: member}} for every folder that holds dictionary files.

	folder is the path inside the repository, "" for its top level. Only <language><slot>.dic names
	count; a name with no language prefix says nothing about which language it is for.
	"""
	folders = {}
	for member in sorted(archive.infolist(), key=lambda member: member.filename):
		parts = member.filename.split("/")
		filename = parts[-1].lower()
		# Every path in a GitHub archive sits under one folder named after the repository and commit.
		if member.is_dir() or len(parts) < 2 or not _SET_FILE.fullmatch(filename):
			continue
		folders.setdefault("/".join(parts[1:-1]), {}).setdefault(filename, member)
	return folders


def _store_set(archive, members, target, set_id, set_name, repository_url, revision):
	"""Write one set beside its previous copy and swap it in. Returns a DownloadedFile per file."""
	previous = {}
	if os.path.isdir(target):
		for filename in os.listdir(target):
			if _SET_FILE.fullmatch(filename.lower()):
				with open(os.path.join(target, filename), "rb") as f:
					previous[filename.lower()] = f.read()
	kept, outcomes = {}, []
	for filename, member in members.items():
		language, slot = filename[:3], filename[3:7]
		try:
			# Checked before reading, so an archive cannot make us unpack something enormous.
			if member.file_size > stack.MAX_FILE_BYTES:
				raise stack.DictionaryFileError("tooLarge")
			data = archive.read(member)
			checked = library.check_file(data, language, slot)
		except stack.DictionaryFileError as error:
			log.error("Rejected %s from %s: %s", filename, set_id, error)
			if filename in previous:
				kept[filename] = previous[filename]
			outcomes.append(
				DownloadedFile(filename, FileStatus.FAILED, error=error.reason, error_line=error.line or 0)
			)
			continue
		kept[filename] = data
		if filename not in previous:
			status = FileStatus.CREATED
		else:
			status = FileStatus.UNCHANGED if previous[filename] == data else FileStatus.UPDATED
		outcomes.append(DownloadedFile(filename, status, reports=checked.reports))
	# A file the source no longer publishes goes away here too, which the old merge could never do.
	outcomes.extend(
		DownloadedFile(filename, FileStatus.REMOVED) for filename in sorted(set(previous) - set(members))
	)
	if kept:
		new_directory = target + ".new"
		shutil.rmtree(new_directory, ignore_errors=True)
		os.makedirs(new_directory)
		for filename, data in kept.items():
			with open(os.path.join(new_directory, filename), "wb") as f:
				f.write(data)
		sets.write_set_ini(new_directory, set_id, set_name, repository_url, revision)
		library.swap_in_directory(new_directory, target)
	return tuple(outcomes)


def set_id_for_source(repository):
	"""The set ID of a source's top-level files, or None if it cannot be used as a source."""
	parsed = parse_repository(repository)
	return stack.set_id_for_repository(*parsed) if parsed else None


def source_set_ids(repository, set_ids):
	"""Which of set_ids came from this source: its top-level set and one per folder inside it."""
	top_level_id = set_id_for_source(repository)
	if top_level_id is None:
		return []
	# A folder inside the repository is its own set, named <repository ID>..<folder>.
	return [
		set_id
		for set_id in sorted(set_ids)
		if set_id == top_level_id or set_id.startswith(top_level_id + "..")
	]


def remove_source(repository, sources_directory):
	"""Delete everything downloaded from a Dictionary Source. Returns the IDs of the sets removed."""
	if not os.path.isdir(sources_directory):
		return ()
	removed = source_set_ids(repository, os.listdir(sources_directory))
	for set_id in removed:
		shutil.rmtree(os.path.join(sources_directory, set_id), ignore_errors=True)
	return tuple(removed)
