import os
import json
import urllib.request
import zipfile
import shutil
import logging
import wx
import re
from io import BytesIO

log = logging.getLogger(__name__)

try:
    from languageHandler import getLanguage
    _ = wx.GetTranslation
except ImportError:
    def _(text):
        return text

class UpdateChangesDialog(wx.Dialog):
    def __init__(self, parent, changes, latest_version):
        super().__init__(parent, title=_("Review Update Changes"), size=(500, 400))
        
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Summary text
        summary = _(f"Update to version {latest_version} includes the following changes:")
        main_sizer.Add(wx.StaticText(self, label=summary), 0, wx.ALL, 10)
        
        # List of changes
        self.list_ctrl = wx.ListCtrl(self, style=wx.LC_REPORT | wx.BORDER_SUNKEN)
        self.list_ctrl.InsertColumn(0, _("Action"), width=100)
        self.list_ctrl.InsertColumn(1, _("File"), width=350)
        
        idx = 0
        for f in changes["added"]:
            self.list_ctrl.InsertItem(idx, _("Add"))
            self.list_ctrl.SetItem(idx, 1, f)
            idx += 1
        for f in changes["modified"]:
            self.list_ctrl.InsertItem(idx, _("Update"))
            self.list_ctrl.SetItem(idx, 1, f)
            idx += 1
        for f in changes["deleted"]:
            self.list_ctrl.InsertItem(idx, _("Delete"))
            self.list_ctrl.SetItem(idx, 1, f)
            idx += 1
            
        main_sizer.Add(self.list_ctrl, 1, wx.EXPAND | wx.ALL, 10)
        
        # Info about preserved files
        if changes["preserved"]:
            p_text = _(f"Note: {len(changes['preserved'])} local configuration/dictionary files will be preserved.")
            main_sizer.Add(wx.StaticText(self, label=p_text), 0, wx.ALL, 10)
            
        # Buttons
        btn_sizer = self.CreateButtonSizer(wx.OK | wx.CANCEL)
        main_sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        
        self.SetSizer(main_sizer)
        self.Layout()

def show_update_dialog(parent, changes, latest_version):
    """
    Shows a dialog with the changes and returns (apply_update, decisions).
    """
    dlg = UpdateChangesDialog(parent, changes, latest_version)
    result = dlg.ShowModal()
    dlg.Destroy()
    
    if result != wx.ID_OK:
        return False, {}
    
    decisions = {}
    for f in changes['added']:
        decisions[f] = "add"
    for f in changes['modified']:
        decisions[f] = "update"
    for f in changes['deleted']:
        decisions[f] = "delete"
        
    return True, decisions

class EloquenceUpdateManager:
    REPO_OWNER = "fastfinge"
    REPO_NAME = "eloquence_64"
    
    def __init__(self, addon_dir):
        self.addon_dir = os.path.abspath(addon_dir)
        self.temp_dir = os.path.join(self.addon_dir, "temp_update")
        self.extract_dir = os.path.join(self.temp_dir, "extracted")
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
            
            # Look for .nvda-addon or .zip in assets
            assets = data.get("assets", [])
            for asset in assets:
                if asset["name"].endswith(".nvda-addon"):
                    download_url = asset["browser_download_url"]
                    break
            
            # If no assets, use the source zip
            if not download_url:
                download_url = data.get("zipball_url")
                
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
        """Downloads the update and returns the path to the zip file"""
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)
            
        zip_path = os.path.join(self.temp_dir, "update.zip")
        
        try:
            headers = {"User-Agent": "NVDA-Eloquence-Updater"}
            req = urllib.request.Request(download_url, headers=headers)
            with urllib.request.urlopen(req) as response:
                total_size = int(response.info().get("Content-Length", 0))
                downloaded = 0
                block_size = 8192
                
                with open(zip_path, "wb") as f:
                    while True:
                        buffer = response.read(block_size)
                        if not buffer:
                            break
                        downloaded += len(buffer)
                        f.write(buffer)
                        if total_size > 0:
                            percent = int(downloaded * 100 / total_size)
                            if not progress_callback(percent, _(f"Downloading update... {percent}%")):
                                raise Exception("Download cancelled by user")
            return zip_path
        except Exception as e:
            log.error(f"Error downloading update: {e}")
            raise

    def extract_update(self, zip_path, progress_callback):
        """Extracts the zip file to a temporary directory"""
        if os.path.exists(self.extract_dir):
            shutil.rmtree(self.extract_dir)
        os.makedirs(self.extract_dir)
        
        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                files = zip_ref.namelist()
                total_files = len(files)
                for i, file in enumerate(files):
                    zip_ref.extract(file, self.extract_dir)
                    percent = int((i + 1) * 100 / total_files)
                    if not progress_callback(percent, _(f"Extracting... {percent}%")):
                        raise Exception("Extraction cancelled by user")
                        
            # If it's a GitHub zipball, it extracts into a subfolder
            contents = os.listdir(self.extract_dir)
            if len(contents) == 1 and os.path.isdir(os.path.join(self.extract_dir, contents[0])):
                # Move everything up one level
                subfolder = os.path.join(self.extract_dir, contents[0])
                for item in os.listdir(subfolder):
                    dest = os.path.join(self.extract_dir, item)
                    if os.path.exists(dest):
                        if os.path.isdir(dest): shutil.rmtree(dest)
                        else: os.remove(dest)
                    shutil.move(os.path.join(subfolder, item), self.extract_dir)
                os.rmdir(subfolder)
                
        except Exception as e:
            log.error(f"Error extracting update: {e}")
            raise

    def analyze_changes(self, progress_callback):
        """
        Analyzes differences between current install and update.
        Returns a dictionary of changes.
        """
        changes = {
            "added": [],
            "modified": [],
            "deleted": [],
            "preserved": [] # Files we want to keep as is
        }
        
        # Files to always preserve (don't overwrite if exist)
        preserve_list = ["ECI.INI", "synthDrivers\\eloquence\\"] # Custom dictionaries
        
        # Walk through the update files
        for root, dirs, files in os.walk(self.extract_dir):
            rel_path = os.path.relpath(root, self.extract_dir)
            if rel_path == ".":
                rel_path = ""
            
            for file in files:
                file_rel_path = os.path.join(rel_path, file)
                current_path = os.path.join(self.addon_dir, "../", file_rel_path)
                
                # Check if it should be preserved
                is_preserved = False
                for p in preserve_list:
                    if file_rel_path.startswith(p):
                        is_preserved = True
                        break
                
                if is_preserved and os.path.exists(current_path):
                    changes["preserved"].append(file_rel_path)
                    continue
                
                if not os.path.exists(current_path):
                    changes["added"].append(file_rel_path)
                else:
                    changes["modified"].append(file_rel_path)
        
        # Check for deleted files
        exclude_from_deletion = [".git", "temp_update", ".venv", "__pycache__", "eloquence"]
        for root, dirs, files in os.walk(self.addon_dir):
            rel_path = os.path.relpath(root, self.addon_dir)
            if rel_path == ".":
                rel_path = ""
            
            if any(rel_path.startswith(e) for e in exclude_from_deletion):
                continue
                
            for file in files:
                file_rel_path = os.path.join(rel_path, file)
                if any(file_rel_path.startswith(e) for e in exclude_from_deletion):
                    continue
                    
                update_path = os.path.join(self.extract_dir, file_rel_path)
                if not os.path.exists(update_path):
                    # Check if it's in preserve list
                    is_preserved = False
                    for p in preserve_list:
                        if file_rel_path.startswith(p):
                            is_preserved = True
                            break
                    if not is_preserved:
                        changes["deleted"].append(file_rel_path)
                        
        progress_callback(100, _("Analysis complete"))
        return changes

    def smart_merge(self, changes, decisions, merge_progress):
        """Applies the changes based on user decisions"""
        total_steps = len(decisions)
        if total_steps == 0:
            merge_progress(100, _("No changes to apply"))
            return

        for i, (file_rel_path, action) in enumerate(decisions.items()):
            src = os.path.join(self.extract_dir, file_rel_path)
            dst = os.path.join(self.addon_dir, "../", file_rel_path)
            
            percent = int((i + 1) * 100 / total_steps)
            merge_progress(percent, _(f"Applying: {file_rel_path}"))
            
            try:
                if action in ("update", "add"):
                    if os.path.isdir(src):
                        if not os.path.exists(dst):
                            os.makedirs(dst)
                    else:
                        dst_dir = os.path.dirname(dst)
                        if not os.path.exists(dst_dir):
                            os.makedirs(dst_dir)
                        shutil.copy2(src, dst)
                elif action == "delete":
                    if os.path.exists(dst):
                        if os.path.isdir(dst):
                            shutil.rmtree(dst)
                        else:
                            os.remove(dst)
            except Exception as e:
                log.error(f"Error applying change to {file_rel_path}: {e}")
        
        merge_progress(100, _("Update complete"))

    def cleanup(self):
        """Removes temporary files"""
        if os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except Exception as e:
                log.error(f"Error cleaning up: {e}")
