import os
import json
import urllib.request
import shutil
import logging
import re
import addonHandler

addonHandler.initTranslation()

log = logging.getLogger(__name__)


class EloquenceUpdateManager:
	REPO_OWNER = "fastfinge"
	REPO_NAME = "eloquence_64"

	def __init__(self, addon_dir):
		self.addon_dir = os.path.abspath(addon_dir)
		self.temp_dir = os.path.join(self.addon_dir, "temp_update")
		self.CURRENT_VERSION = self._get_current_version()

	def _get_current_version(self):
		manifest_path = os.path.join(self.addon_dir, "../manifest.ini")
		if not os.path.exists(manifest_path):
			return "0.0.0"

		try:
			with open(manifest_path, "r", encoding="utf-8") as f:
				for line in f:
					if line.startswith("version"):
						return line.split("=")[1].strip()
		except Exception as e:
			log.error(f"Error reading manifest: {e}")
		return "0.0.0"

	def check_for_updates(self):
		"""
		Checks GitHub for the latest release.
		Returns (has_update, latest_version, download_url, changelog)
		"""
		api_url = f"https://api.github.com/repos/{self.REPO_OWNER}/{self.REPO_NAME}/releases/latest"
		try:
			headers = {"User-Agent": "NVDA-Eloquence-Updater"}
			req = urllib.request.Request(api_url, headers=headers)
			with urllib.request.urlopen(req) as response:
				data = json.loads(response.read().decode())

			latest_version = data.get("tag_name", "0.0.0").lstrip("v")
			download_url = None

			# Standard NVDA installation requires a packaged add-on bundle.
			assets = data.get("assets", [])
			for asset in assets:
				if asset["name"].endswith(".nvda-addon"):
					download_url = asset["browser_download_url"]
					break

			if not download_url:
				raise RuntimeError(_("Latest release does not include an NVDA add-on package."))

			changelog = data.get("body", "No changelog provided.")

			has_update = self._is_newer(latest_version, self.CURRENT_VERSION)
			return has_update, latest_version, download_url, changelog

		except Exception as e:
			log.error(f"Error checking for updates: {e}")
			raise

	def _is_newer(self, latest, current):
		# Simple version comparison
		# Handles date-based versions like 0.20250420.01
		def parse_version(v):
			return [int(x) for x in re.findall(r"\d+", v)]

		try:
			return parse_version(latest) > parse_version(current)
		except Exception:
			return latest != current

	def download_update(self, download_url, progress_callback):
		"""Downloads the update and returns the path to the add-on package."""
		if not os.path.exists(self.temp_dir):
			os.makedirs(self.temp_dir)

		addon_path = os.path.join(self.temp_dir, "update.nvda-addon")

		try:
			headers = {"User-Agent": "NVDA-Eloquence-Updater"}
			req = urllib.request.Request(download_url, headers=headers)
			with urllib.request.urlopen(req) as response:
				total_size = int(response.info().get("Content-Length", 0))
				downloaded = 0
				block_size = 8192

				with open(addon_path, "wb") as f:
					while True:
						buffer = response.read(block_size)
						if not buffer:
							break
						downloaded += len(buffer)
						f.write(buffer)
						if total_size > 0:
							percent = int(downloaded * 100 / total_size)
							# Translators: Text in the progress dialog used during add-on update.
							if not progress_callback(
								percent, _("Downloading update... {percent}%").format(percent=percent)
							):
								raise Exception("Download cancelled by user")
			return addon_path
		except Exception as e:
			log.error(f"Error downloading update: {e}")
			raise

	def install_update(self, addon_path, parent=None):
		"""Installs the downloaded package through NVDA's add-on install machinery."""
		try:
			from addonStore.install import installAddon
		except ImportError:
			from gui import addonGui

			return addonGui.installAddon(parent, addon_path)

		installAddon(addon_path)
		return True

	def prompt_for_restart(self):
		from gui.addonGui import promptUserForRestart

		promptUserForRestart()

	def cleanup(self):
		"""Removes temporary files"""
		if os.path.exists(self.temp_dir):
			try:
				shutil.rmtree(self.temp_dir)
			except Exception as e:
				log.error(f"Error cleaning up: {e}")
