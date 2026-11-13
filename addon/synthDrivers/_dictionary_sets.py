"""Managed Dictionary Sets: find the dictionary sets that add-ons provide and that this add-on downloaded.

A Managed Dictionary Set is one upstream dataset, a folder holding a set.ini and one
<language><slot>.dic file per language and slot. Other add-ons, such as EloquenceDictionaryManager,
provide sets through its directory contract, version 1: an installed add-on with
dictionaries/contract.ini and dictionaries/sets/<set-id>/. This add-on stores its own downloads in
the same layout, so one reader serves both.

Everything here fails closed and per set: a set that is malformed is ignored and logged, and its
valid neighbours stay available. No NVDA dependencies; the caller supplies the add-on folders.
"""

import configparser
import logging
import os
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

CONTRACT_FORMAT = "eci-dictionary-sets"
SUPPORTED_CONTRACT_VERSIONS = frozenset({1})

_SET_ID = re.compile(r"[a-z0-9][a-z0-9.-]*")
_DICTIONARY_FILE = re.compile(r"([a-z]{3})(main|root|abbr)\.dic")
# A set an add-on ships must describe where it came from and how it is licensed. A copy the user
# downloaded is not redistributed, so it needs only enough to name it.
_PROVIDER_FIELDS = (
	"id",
	"name",
	"source_url",
	"source_version",
	"source_revision",
	"attribution",
	"license",
	"license_url",
)
_DOWNLOAD_FIELDS = ("id", "name", "source_url")


@dataclass(frozen=True)
class DictionarySet:
	id: str
	name: str
	directory: str
	# True when an installed add-on provides it; False for this add-on's own download.
	installed: bool
	# The upstream release it was taken from, or "" for a download of a repository's default branch.
	version: str = ""
	revision: str = ""
	source_url: str = ""
	# {(language, slot): path}, for example ("enu", "root").
	files: dict = field(default_factory=dict, compare=False)

	@property
	def languages(self):
		return frozenset(language for language, _ in self.files)


def discover_sets(provider_directories, downloads_directory):
	"""Return {set ID: DictionarySet} from installed providers and from our own downloads.

	When an add-on provides a set this add-on also downloaded, the add-on's copy is the one used:
	the two are the same dataset, so stacking both would apply it twice.
	"""
	found = {}
	for provider in provider_directories:
		for dictionary_set in _provider_sets(provider):
			if dictionary_set.id in found:
				log.warning(
					"Ignoring %s from %s: another add-on already provides it", dictionary_set.id, provider
				)
				continue
			found[dictionary_set.id] = dictionary_set
	for directory in _subdirectories(downloads_directory):
		dictionary_set = read_set(directory, installed=False)
		if dictionary_set is not None:
			found.setdefault(dictionary_set.id, dictionary_set)
	return found


def _provider_sets(provider_directory):
	dictionaries = os.path.join(provider_directory, "dictionaries")
	marker = os.path.join(dictionaries, "contract.ini")
	if not os.path.isfile(marker):
		return
	try:
		contract = _read_ini(marker)["contract"]
		contract_format, version = contract["format"], int(contract["version"])
	except (OSError, UnicodeDecodeError, configparser.Error, KeyError, ValueError) as error:
		log.warning("Ignoring the dictionary provider at %s: %r", provider_directory, error)
		return
	if contract_format != CONTRACT_FORMAT or version not in SUPPORTED_CONTRACT_VERSIONS:
		log.warning(
			"Ignoring the dictionary provider at %s: unsupported contract %s version %s",
			provider_directory,
			contract_format,
			version,
		)
		return
	for directory in _subdirectories(os.path.join(dictionaries, "sets")):
		dictionary_set = read_set(directory, installed=True)
		if dictionary_set is not None:
			yield dictionary_set


def read_set(directory, installed):
	"""Read one set folder, or return None, having logged why, if it is not a valid set."""
	try:
		metadata = _read_ini(os.path.join(directory, "set.ini"))["set"]
	except (OSError, UnicodeDecodeError, configparser.Error, KeyError) as error:
		log.warning("Ignoring the dictionary set at %s: %r", directory, error)
		return None
	missing = [
		name for name in (_PROVIDER_FIELDS if installed else _DOWNLOAD_FIELDS) if not metadata.get(name)
	]
	if missing:
		log.warning("Ignoring the dictionary set at %s: set.ini lacks %s", directory, ", ".join(missing))
		return None
	set_id = metadata["id"]
	if not _SET_ID.fullmatch(set_id) or set_id != os.path.basename(os.path.normpath(directory)):
		log.warning(
			"Ignoring the dictionary set at %s: its ID %r does not name its folder", directory, set_id
		)
		return None
	files = {}
	for name in sorted(os.listdir(directory)):
		# Anything else in the folder belongs to a later version of the contract.
		match = _DICTIONARY_FILE.fullmatch(name.lower())
		if match:
			files[match.group(1), match.group(2)] = os.path.join(directory, name)
	return DictionarySet(
		id=set_id,
		name=metadata["name"],
		directory=directory,
		installed=installed,
		version=metadata.get("source_version", ""),
		revision=metadata.get("source_revision", ""),
		source_url=metadata.get("source_url", ""),
		files=files,
	)


def write_set_ini(directory, set_id, name, source_url, revision=""):
	"""Describe a set this add-on downloaded, with the fields the contract asks of a download."""
	metadata = configparser.ConfigParser(interpolation=None)
	metadata["set"] = {"id": set_id, "name": name, "source_url": source_url}
	if re.fullmatch(r"[0-9a-f]{40}", revision):
		metadata["set"]["source_revision"] = revision
	with open(os.path.join(directory, "set.ini"), "w", encoding="utf-8", newline="\n") as f:
		metadata.write(f)


def _read_ini(path):
	parser = configparser.ConfigParser(interpolation=None)
	with open(path, encoding="utf-8-sig") as f:
		parser.read_file(f)
	return parser


def _subdirectories(directory):
	try:
		names = sorted(os.listdir(directory))
	except OSError:
		return []
	return [path for path in (os.path.join(directory, name) for name in names) if os.path.isdir(path)]
