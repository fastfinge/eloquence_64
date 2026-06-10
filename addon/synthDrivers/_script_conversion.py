"""Script Conversion for the Mandarin Chinese Voice Identity."""

from functools import lru_cache
from pathlib import Path

_DATA_DIR = Path(__file__).with_name("t2s_data")
_TABLE_NAMES = ("TSCharacters.txt", "TSPhrases.txt")


def convert_traditional_to_simplified(text):
	"""Convert Traditional Chinese text to Simplified using vendored OpenCC data."""
	if not text:
		return text
	mappings, max_key_length = _load_t2s_dictionary()
	result = []
	position = 0
	text_length = len(text)
	while position < text_length:
		max_length = min(max_key_length, text_length - position)
		for key_length in range(max_length, 0, -1):
			key = text[position : position + key_length]
			replacement = mappings.get(key)
			if replacement is not None:
				result.append(replacement)
				position += key_length
				break
		else:
			result.append(text[position])
			position += 1
	return "".join(result)


@lru_cache(maxsize=1)
def _load_t2s_dictionary():
	mappings = {}
	max_key_length = 0
	for table_name in _TABLE_NAMES:
		table_mappings, table_max_key_length = _parse_opencc_table(_DATA_DIR / table_name)
		mappings.update(table_mappings)
		max_key_length = max(max_key_length, table_max_key_length)
	if not mappings:
		raise RuntimeError("OpenCC Script Conversion tables are empty")
	return mappings, max_key_length


def _parse_opencc_table(path):
	mappings = {}
	max_key_length = 0
	with path.open(encoding="utf-8") as table:
		for line_number, line in enumerate(table, start=1):
			line = line.strip()
			if not line or line.startswith("#"):
				continue
			try:
				key, values = line.split("\t", 1)
			except ValueError as error:
				raise ValueError(f"{path.name}:{line_number}: expected tab-separated key and values") from error
			value = values.split(" ", 1)[0]
			mappings[key] = value
			max_key_length = max(max_key_length, len(key))
	return mappings, max_key_length
