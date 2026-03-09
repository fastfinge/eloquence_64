# Build customizations
# Change this file instead of SConstruct or manifest files, whenever possible.

import subprocess


def _get_version():
	"""Derive addon version from git tags.

	- On exact tag: returns tag name (e.g. "v14")
	- Between tags: returns describe output (e.g. "v13-2-gabcdef")
	- No git / no tags: returns "dev"
	"""
	try:
		result = subprocess.run(
			["git", "describe", "--tags"],
			capture_output=True,
			text=True,
		)
		if result.returncode == 0:
			return result.stdout.strip()
	except FileNotFoundError:
		pass
	return "dev"


addon_info = {
	"addon_name": "Eloquence",
	"addon_summary": "Eloquence Synthesizer",
	"addon_description": "Eloquence synthesizer for NVDA with 64-bit support",
	"addon_version": _get_version(),
	"addon_author": "NVDA User",
	"addon_url": "https://github.com/pumper42nickel/eloquence_threshold",
	"addon_lastTestedNVDAVersion": "2034.1",
}
