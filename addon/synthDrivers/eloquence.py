# Copyright (C) 2009-2019 eloquence fans
# synthDrivers/eci.py
# todo: possibly add to this
import speech, tones, gui, wx
import ctypes, winsound, shutil # Added for Copy Helper tool

try:
    from speech import (
        IndexCommand,
        CharacterModeCommand,
        LangChangeCommand,
        BreakCommand,
        PitchCommand,
        RateCommand,
        VolumeCommand,
        PhonemeCommand,
    )
except ImportError:
    from speech.commands import (
        IndexCommand,
        CharacterModeCommand,
        LangChangeCommand,
        BreakCommand,
        PitchCommand,
        RateCommand,
        VolumeCommand,
        PhonemeCommand,
    )

try:
    from driverHandler import NumericDriverSetting, BooleanDriverSetting, DriverSetting
except ImportError:
    from autoSettingsUtils.driverSetting import (
        BooleanDriverSetting,
        DriverSetting,
        NumericDriverSetting,
    )

try:
    from autoSettingsUtils.utils import StringParameterInfo
except ImportError:
    class StringParameterInfo:
        def __init__(self, value, label):
            self.value = value
            self.label = label


punctuation = ",.?:;)(?!" 
punctuation = [x for x in punctuation]
from ctypes import *
import ctypes.wintypes
from ctypes import wintypes
import synthDriverHandler, os, config, re, nvwave, threading, logging, driverHandler
from synthDriverHandler import (
    SynthDriver,
    VoiceInfo,
    synthIndexReached,
    synthDoneSpeaking,
)
from synthDriverHandler import SynthDriver, VoiceInfo
from . import _eloquence
from collections import OrderedDict
import unicodedata

log = logging.getLogger(__name__)


minRate = 40
maxRate = 150
pause_re = re.compile(r"([a-zA-Z0-9]|\s)([,.:;?!)])(\2*?)(\s|[\\/]|$|$)")
time_re = re.compile(r"(\d):(\d+):(\d+)")
english_fixes = {
    re.compile(r"(\w+)\.([a-zA-Z]+)"): r"\1 dot \2",
    re.compile(r"([a-zA-Z0-9_]+)@(\w+)"): r"\1 at \2",
    # Does not occur in normal use, however if a dictionary entry contains the Mc prefix, and NVDA splits it up, the synth will crash.
    re.compile(r"\b(Mc)\s+([A-Z][a-z]+)"): r"\1\2",
    re.compile(r"\b(.*?)c(ae|\xe6)sur(e)?", re.I): r"\1seizur",
    re.compile(r"\b(|\d+|\W+)h'(r|v)[e]", re.I): r"\1h \2e",
    re.compile(
        r"\b(\w+[bdfhjlmnqrvz])(h[he]s)([abcdefghjklmnopqrstvwy]\w+)\b", re.I
    ): r"\1 \2\3",
    re.compile(r"\b(\w+[bdfhjlmnqrvz])(h[he]s)(iron+[degins]?)", re.I): r"\1 \2\3",
    re.compile(r"(\d):(\d\d[snrt][tdh])", re.I): r"\1 \2",
    re.compile(
        r"\b([bcdfghjklmnpqrstvwxz]+)'([bcdefghjklmnprstvwxz']+)'([drtv][aeiou]?)", re.I
    ): r"\1 \2 \3",
    re.compile(r"\b(you+)'(re)+'([drv]e?)", re.I): r"\1 \2 \3",
    re.compile(r"(re|un|non|anti)cosp", re.I): r"\1kosp",
    re.compile(r"(EUR[A-Z]+)(\d+)", re.I): r"\1 \2",
    re.compile(r"\b(\d+|\W+|[bcdfghjklmnpqrstvwxz])?t+z[s]che", re.I): r"\1tz sche",
    re.compile(r"\b(juar[aeou]s)([aeiou]{6,})", re.I): r"\1 \2",
}
french_fixes = {
    re.compile(r"([a-zA-Z0-9_]+)@(\w+)"): r"\1 arobase \2",
}
spanish_fixes = {
    # for emails
    re.compile(r"([a-zA-Z0-9_]+)@(\w+)"): r"\1 arroba \2",
}
german_fixes = {
    # Crash words
    re.compile(r"dane-ben", re.I): r"dane- ben",
    re.compile(r"dage-gen", re.I): r"dage- gen",
}
VOICE_BCP47 = {
    "enu": "en-US",
    "eng": "en-GB",
    "esp": "es-ES",
    "esm": "es-419",
    "ptb": "pt-BR",
    "fra": "fr-FR",
    "frc": "fr-CA",
    "deu": "de-DE",
    "ita": "it-IT",
    "fin": "fi-FI",
    "chs": "zh-CN",  # Simplified Chinese
    "jpn": "ja-JP",  # Japanese
    "kor": "ko-KR",  # Korean
}

VOICE_CODE_TO_ID = {code: str(info[0]) for code, info in _eloquence.langs.items()}
VOICE_ID_TO_BCP47 = {
    voice_id: VOICE_BCP47.get(code)
    for code, voice_id in VOICE_CODE_TO_ID.items()
    if VOICE_BCP47.get(code)
}
LANGUAGE_TO_VOICE_ID = {
    lang.lower(): VOICE_CODE_TO_ID[code]
    for code, lang in VOICE_BCP47.items()
    if code in VOICE_CODE_TO_ID
}
PRIMARY_LANGUAGE_TO_VOICE_IDS = {}
for code, lang in VOICE_BCP47.items():
    voice_id = VOICE_CODE_TO_ID.get(code)
    if not voice_id:
        continue
    primary = lang.split("-", 1)[0].lower()
    PRIMARY_LANGUAGE_TO_VOICE_IDS.setdefault(primary, []).append(voice_id)

variants = {
    1: "Reed",
    2: "Shelley",
    3: "Bobby",
    4: "Rocko",
    5: "Glen",
    6: "Sandy",
    7: "Grandma",
    8: "Grandpa",
}


def strip_accents(s):
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def normalizeText(s):
    """
    Normalizes  text by removing unicode characters.
    Tries to preserve accented characters if they fall into MBCS encoding page.
    Tries to find closest ASCII characters if accented characters cannot be represented in MBCS.
    """
    result = []
    for c in s:
        try:
            cc = c.encode("mbcs").decode("mbcs")
        except UnicodeEncodeError:
            cc = strip_accents(c)
            try:
                cc.encode("mbcs")
            except UnicodeEncodeError:
                cc = "?"
        result.append(cc)
    return "".join(result)


def resub(dct, s):
    for r in dct.keys():
        s = r.sub(dct[r], s)
    return s


class EloquenceSettingsPanel(gui.settingsDialogs.SettingsPanel):
    title = _("Eloquence")

    def makeSettings(self, settings):
        try:
            sHelper = gui.guiHelper.BoxSizerHelper(self, sizer=settings)

            self.dictionarySources = {
                "https://github.com/mohamed00/AltIBMTTSDictionaries": "Alternative IBM TTS Dictionaries",
                "https://github.com/eigencrow/IBMTTSDictionaries": "IBM TTS Dictionaries",
            }

            self.dictionaryChoice = sHelper.addLabeledControl(
                _("Dictionary:"), wx.Choice, choices=list(self.dictionarySources.values())
            )
            self.dictionaryChoice.SetStringSelection(
                config.conf.get("eloquence", {}).get(
                    "dictionary_name", "Alternative IBM TTS Dictionaries"
                )
            )

            self.updateButton = sHelper.addItem(
                wx.Button(self, label=_("Check for updates"))
            )
            self.Bind(wx.EVT_BUTTON, self.onUpdate, self.updateButton)

            # Tool to automate copying eloquence_host32.exe for 64-bit NVDA secure screens
            self.copyHelperButton = sHelper.addItem(
                wx.Button(
                    self, label=_("Copy Helper to System Config (for Logon Screen)")
                )
            )
            self.Bind(wx.EVT_BUTTON, self.onCopyHelper, self.copyHelperButton)

            # NEW: Auto-update addon button
            self.addonUpdateButton = sHelper.addItem(
                wx.Button(self, label=_("Check for Add-on Updates"))
            )
            self.Bind(wx.EVT_BUTTON, self.onCheckAddonUpdate, self.addonUpdateButton)
        except Exception as e:
            log.error(f"Error creating Eloquence settings panel: {e}")
            # Panel creation failed, but don't crash - synth will still work

    def onCopyHelper(self, evt):
        """Copies eloquence_host32.exe with UAC elevation support and definitive feedback."""
        source_file = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "eloquence_host32.exe")
        )
        prog_files = os.environ.get("ProgramFiles", "C:\\Program Files")
        target_addon_dir = os.path.normpath(
            os.path.join(prog_files, "NVDA", "systemConfig", "addons", "Eloquence")
        )

        # Security check: Ensure the target addon directory exists in systemConfig
        if not os.path.isdir(target_addon_dir):
            wx.MessageBox(
                _(
                    "Eloquence folder not found in systemConfig.\n\nPlease go to NVDA Settings > General and click 'Use currently saved settings during sign-in' first to initialize folders."
                ),
                _("Folder Missing"),
                wx.OK | wx.ICON_WARNING,
            )
            return

        dest_dir = os.path.normpath(os.path.join(target_addon_dir, "synthDrivers"))
        dest_file = os.path.normpath(os.path.join(dest_dir, "eloquence_host32.exe"))

        if not os.path.exists(source_file):
            wx.MessageBox(
                _(f"Source file not found at:\n{source_file}"),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
            )
            return

        # Prepare elevated command: ensure subdirectory exists and copy the helper
        cmd_params = (
            f'/c mkdir "{dest_dir}" 2>nul & copy /y "{source_file}" "{dest_file}"'
        )

        try:
            # Triggering UAC Elevation using ShellExecuteW's "runas" verb
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", "cmd.exe", cmd_params, None, 0
            )

            if ret > 32:
                # Play Windows Asterisk sound for confirmation of successful launch
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
                wx.MessageBox(
                    _(
                        "Successfully copied eloquence_host32.exe to systemConfig!\n\nEloquence should now load normally on logon screen, start-up, and other secure screens."
                    ),
                    _("Success"),
                    wx.OK | wx.ICON_INFORMATION,
                )
            elif ret == 5:
                # SE_ERR_ACCESSDENIED: Elevation prompt was declined
                wx.MessageBox(
                    _(
                        "Copy process was cancelled or permission was denied by the user."
                    ),
                    _("Cancelled"),
                    wx.OK | wx.ICON_ERROR,
                )
            else:
                wx.MessageBox(
                    _(
                        f"An error occurred while attempting to copy the file. (Error Code: {ret})"
                    ),
                    _("Error"),
                    wx.OK | wx.ICON_ERROR,
                )
        except Exception as e:
            wx.MessageBox(
                _(f"An unexpected error occurred: {str(e)}"),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
            )

    def onCheckAddonUpdate(self, evt):
        """Check for and apply addon updates from GitHub"""
        import sys
        import os

        # Import the update manager
        addon_dir = os.path.abspath(os.path.dirname(__file__))
        update_manager_path = os.path.join(addon_dir, "_eloquence_updater.py")

        # Check if updater exists
        if not os.path.exists(update_manager_path):
            wx.MessageBox(
                _("Update manager not found. Please reinstall the add-on."),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
            )
            return

        # Import update manager
        sys.path.insert(0, addon_dir)
        try:
            from _eloquence_updater import EloquenceUpdateManager, show_update_dialog
        except ImportError as e:
            wx.MessageBox(
                _(f"Failed to load update manager: {e}"),
                _("Error"),
                wx.OK | wx.ICON_ERROR,
            )
            return
        finally:
            if addon_dir in sys.path:
                sys.path.remove(addon_dir)

        # Create progress dialog
        progress = wx.ProgressDialog(
            _("Checking for Updates"),
            _("Connecting to GitHub..."),
            maximum=100,
            parent=self,
            style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE,
        )

        try:
            # Initialize update manager
            manager = EloquenceUpdateManager(addon_dir)

            # Check for updates
            progress.Update(10, _("Checking for updates..."))
            (
                has_update,
                latest_version,
                download_url,
                changelog,
            ) = manager.check_for_updates()

            if not has_update:
                progress.Update(100, _("No updates available"))
                progress.Destroy()
                wx.MessageBox(
                    _("You are using the latest version!"),
                    _("Up to Date"),
                    wx.OK | wx.ICON_INFORMATION,
                )
                return

            # Show changelog
            progress.Update(20, _("Update available!"))
            progress.Destroy()

            changelog_dialog = wx.MessageDialog(
                self,
                _(
                    f"New version available: {latest_version}\n\n"
                    f"Current version: {manager.CURRENT_VERSION}\n\n"
                    f"Changelog:\n{changelog[:500]}\n\n"
                    f"Would you like to download and review the update?"
                ),
                _("Update Available"),
                wx.YES_NO | wx.ICON_INFORMATION,
            )

            if changelog_dialog.ShowModal() != wx.ID_YES:
                return

            # Download update
            progress = wx.ProgressDialog(
                _("Downloading Update"),
                _("Downloading..."),
                maximum=100,
                parent=self,
                style=wx.PD_APP_MODAL | wx.PD_CAN_ABORT,
            )

            def download_progress(percent, message):
                cont, skip = progress.Update(percent, message)
                return cont

            zip_path = manager.download_update(download_url, download_progress)

            # Extract update
            progress.Update(0, _("Extracting update..."))
            manager.extract_update(zip_path, download_progress)

            # Analyze changes
            progress.Update(0, _("Analyzing changes..."))
            changes = manager.analyze_changes(download_progress)

            progress.Destroy()

            # Show update dialog with detailed changes
            apply_update, decisions = show_update_dialog(self, changes, latest_version)

            if not apply_update:
                manager.cleanup()
                wx.MessageBox(
                    _("Update cancelled."), _("Cancelled"), wx.OK | wx.ICON_INFORMATION
                )
                return

            # Apply update with progress
            progress = wx.ProgressDialog(
                _("Applying Update"),
                _("Please wait..."),
                maximum=100,
                parent=self,
                style=wx.PD_APP_MODAL | wx.PD_AUTO_HIDE,
            )

            def merge_progress(percent, message):
                progress.Update(percent, message)

            manager.smart_merge(changes, decisions, merge_progress)

            progress.Destroy()

            # Success!
            wx.MessageBox(
                _(
                    f"Update to {latest_version} applied successfully!\n\n"
                    f"Please restart NVDA for changes to take effect."
                ),
                _("Update Successful"),
                wx.OK | wx.ICON_INFORMATION,
            )

            # Cleanup
            manager.cleanup()

        except Exception as e:
            progress.Destroy()
            log.error(f"Update failed: {e}")
            wx.MessageBox(
                _(
                    f"Update failed: {str(e)}\n\n" f"Your addon has not been modified."
                ),
                _("Update Failed"),
                wx.OK | wx.ICON_ERROR,
            )

    def onSave(self):
        if "eloquence" not in config.conf:
            config.conf["eloquence"] = {}
        selection = self.dictionaryChoice.GetStringSelection()
        for url, name in self.dictionarySources.items():
            if name == selection:
                config.conf["eloquence"]["dictionary_name"] = name
                config.conf["eloquence"]["dictionary_url"] = url
                break

    def onUpdate(self, evt):
        import urllib.request, zipfile, os, shutil, globalPluginHandler
        from scriptHandler import script

        self.onSave()
        dictionary_url = config.conf.get("eloquence", {}).get("dictionary_url")
        if not dictionary_url:
            wx.MessageBox(
                "Please select a dictionary first.", "Error", wx.OK | wx.ICON_ERROR
            )
            return

        try:
            # Add /archive/master.zip to the end of the URL to download the master branch
            zip_url = dictionary_url + "/archive/master.zip"
            zip_path, _ = urllib.request.urlretrieve(zip_url)

            addon_dir = os.path.abspath(os.path.dirname(__file__))
            dest_folder = os.path.join(addon_dir, "eloquence")

            if not os.path.exists(dest_folder):
                os.makedirs(dest_folder)

            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(addon_dir)
                zip_contents = zip_ref.namelist()
                extracted_root_name = zip_contents[0].split("/")[0]
                extracted_folder_path = os.path.join(addon_dir, extracted_root_name)

            updates_count = 0

            # --- HELPER: Ensure CP1252 compatibility ---
            def clean_key_text(text):
                try:
                    text.encode("cp1252")
                    return text
                except UnicodeEncodeError:
                    # If not CP1252, fallback to stripping accents
                    return "".join(
                        c
                        for c in unicodedata.normalize("NFD", text)
                        if unicodedata.category(c) != "Mn"
                    )

            # --- HELPER: Extract Key/Word only (Cleaned) ---
            def get_key(line):
                parts = line.strip().split(None, 1)
                if parts:
                    raw_key = parts[0].lower()
                    return clean_key_text(raw_key)  # Return CP1252-safe key
                return None

            # --- HELPER: Normalize Format (Space to Tab + Clean ALL text) ---
            def normalize_entry_format(line):
                line = line.strip()
                if " [" in line and "\t[" not in line:
                    parts = line.split(" [", 1)
                    if len(parts) == 2:
                        word_part = parts[0].strip()
                        pronunciation_part = parts[1]
                        # Clean BOTH the word and pronunciation to ensure CP1252 compatibility
                        clean_word = clean_key_text(word_part)
                        clean_pronunciation = clean_key_text(pronunciation_part)
                        return f"{clean_word}\t[{clean_pronunciation}"

                # Even if it's already tabbed, ensure ALL text is CP1252-safe
                if "\t[" in line:
                    parts = line.split("\t[", 1)
                    if len(parts) == 2:
                        word_part = parts[0].strip()
                        pronunciation_part = parts[1]
                        # Clean BOTH parts
                        clean_word = clean_key_text(word_part)
                        clean_pronunciation = clean_key_text(pronunciation_part)
                        return f"{clean_word}\t[{clean_pronunciation}"

                # If no bracket format, just clean the whole line
                return clean_key_text(line)

            # --- MAIN LOGIC ---
            if os.path.exists(extracted_folder_path):

                candidates = []
                for root, dirs, files in os.walk(extracted_folder_path):
                    for f in files:
                        if f.lower().endswith(".dic"):
                            full_path = os.path.join(root, f)
                            candidates.append((full_path, f))

                processed_filenames = set()
                encodings_to_try = ["utf-8", "cp1252", "iso-8859-1", "cp437"]

                for source_path, filename in candidates:
                    dest_path = os.path.join(dest_folder, filename)

                    # Auto-create new dictionary files with CP1252-safe content
                    if not os.path.exists(dest_path):
                        try:
                            # Read source file with encoding detection
                            source_lines = []
                            read_success = False
                            for enc in encodings_to_try:
                                try:
                                    with open(source_path, "r", encoding=enc) as f:
                                        source_lines = f.readlines()
                                        read_success = True
                                        break
                                except UnicodeDecodeError:
                                    continue

                            if not read_success:
                                with open(
                                    source_path, "r", encoding="iso-8859-1", errors="replace"
                                ) as f:
                                    source_lines = f.readlines()

                            # Process and strip accents from all lines
                            processed_lines = []
                            for line in source_lines:
                                normalized_line = normalize_entry_format(line)
                                if normalized_line.strip():  # Skip empty lines
                                    processed_lines.append(normalized_line)

                            # Write as CP1252
                            with open(dest_path, "w", encoding="cp1252") as f:
                                for line in processed_lines:
                                    f.write(f"{line}\n")

                            updates_count += len(processed_lines)
                            log.info(
                                f"Created new dictionary file: {filename} ({len(processed_lines)} entries, CP1252-safe)"
                            )
                        except Exception as e:
                            log.error(f"Failed to create new dictionary {filename}: {e}")
                        continue

                    if filename.lower() in processed_filenames:
                        continue
                    processed_filenames.add(filename.lower())

                    lines_to_append = []
                    try:
                        # 1. READ LOCAL: Extract CLEAN KEYS
                        existing_keys = set()

                        def load_local_keys(f_handle):
                            for line in f_handle:
                                key = get_key(line)
                                if key:
                                    existing_keys.add(key)

                        try:
                            # Try CP1252 first as it is the standard for dictionaries
                            with open(dest_path, "r", encoding="cp1252") as f:
                                load_local_keys(f)
                        except UnicodeDecodeError:
                            # Fallback if it was previously written in a different encoding
                            try:
                                with open(dest_path, "r", encoding="utf-8") as f:
                                    load_local_keys(f)
                            except UnicodeDecodeError:
                                with open(
                                    dest_path, "r", encoding="mbcs", errors="ignore"
                                ) as f:
                                    load_local_keys(f)

                        # 2. READ SOURCE WITH AUTO-DETECT
                        source_lines = []
                        read_success = False
                        for enc in encodings_to_try:
                            try:
                                with open(source_path, "r", encoding=enc) as f:
                                    source_lines = f.readlines()
                                    read_success = True
                                    break
                            except UnicodeDecodeError:
                                continue

                        if not read_success:
                            with open(
                                source_path, "r", encoding="iso-8859-1", errors="replace"
                            ) as f:
                                source_lines = f.readlines()

                        # 3. FILTER, CLEAN, & FORMAT
                        for line in source_lines:
                            # This cleans the visual word and normalizes spaces while preserving CP1252 accents
                            normalized_line = normalize_entry_format(line)

                            # Extract the clean key for comparison
                            key = get_key(normalized_line)

                            if not key:
                                continue

                            # Check duplicates using the key
                            if key not in existing_keys:
                                lines_to_append.append(normalized_line)
                                existing_keys.add(key)

                        # 4. WRITE UPDATES (Strictly CP1252)
                        if lines_to_append:
                            with open(dest_path, "a", encoding="cp1252") as f:
                                f.write("\n")
                                for item in lines_to_append:
                                    f.write(f"{item}\n")
                            updates_count += len(lines_to_append)

                    except Exception as e:
                        log.error(f"Failed to merge dictionary {filename}: {e}")

                shutil.rmtree(extracted_folder_path)

            os.remove(zip_path)

            if updates_count > 0:
                # Count how many were new files vs updated entries
                new_files = sum(
                    1 for f in os.listdir(dest_folder) if f.lower().endswith(".dic")
                )
                wx.MessageBox(
                    f"Dictionary update successful!\n\n"
                    f"• Total updates: {updates_count}\n"
                    f"• Dictionary files: {new_files}\n\n"
                    f"Note: CP1252 encoding enforced; some accents may have been stripped for compatibility.",
                    "Success",
                    wx.OK | wx.ICON_INFORMATION,
                )
            else:
                wx.MessageBox(
                    "No new updates found. Your dictionaries are already up to date.",
                    "Eloquence",
                    wx.OK | wx.ICON_INFORMATION,
                )

        except Exception as e:
            wx.MessageBox(
                f"An error occurred while updating the dictionary: {e}",
                "Error",
                wx.OK | wx.ICON_ERROR,
            )
        pass


class SynthDriver(synthDriverHandler.SynthDriver):
    settingsPanel = EloquenceSettingsPanel
    supportedSettings = (
        SynthDriver.VoiceSetting(),
        SynthDriver.VariantSetting(),
        SynthDriver.RateSetting(),
        SynthDriver.PitchSetting(),
        SynthDriver.InflectionSetting(),
        SynthDriver.VolumeSetting(),
        NumericDriverSetting("hsz", "Head Size"),
        NumericDriverSetting("rgh", "Roughness"),
        NumericDriverSetting("bth", "Breathiness"),
        BooleanDriverSetting(
            "backquoteVoiceTags", "Enable backquote voice &tags", True
        ),
        BooleanDriverSetting("ABRDICT", "Enable &abbreviation dictionary", False),
        BooleanDriverSetting("phrasePrediction", "Enable phrase prediction", False),
        DriverSetting("pauseMode", "Pauses", defaultVal="0"),
    )
    supportedCommands = {
        IndexCommand,
        CharacterModeCommand,
        LangChangeCommand,
        BreakCommand,
        PitchCommand,
        RateCommand,
        VolumeCommand,
        PhonemeCommand,
    }
    supportedNotifications = {synthIndexReached, synthDoneSpeaking}
    PROSODY_ATTRS = {
        PitchCommand: _eloquence.pitch,
        VolumeCommand: _eloquence.vlm,
        RateCommand: _eloquence.rate,
    }

    description = "ETI-Eloquence"
    name = "eloquence"

    # Initialize _pause_mode at class level to prevent issues with setting restoration
    _pause_mode = 0

    @classmethod
    def check(cls):
        try:
            log.info("Eloquence: Running check() to verify synth is available")
            result = _eloquence.eciCheck()
            log.info(f"Eloquence: check() returned {result}")
            return result
        except Exception as e:
            log.error(f"Eloquence: check() failed with error: {e}", exc_info=True)
            return False

    def __init__(self):
        # Safe settings panel registration - won't crash if API changes in different NVDA versions
        try:
            if hasattr(gui.settingsDialogs, "NVDASettingsDialog"):
                if hasattr(gui.settingsDialogs.NVDASettingsDialog, "categoryClasses"):
                    if (
                        EloquenceSettingsPanel
                        not in gui.settingsDialogs.NVDASettingsDialog.categoryClasses
                    ):
                        gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(
                            EloquenceSettingsPanel
                        )
        except Exception as e:
            log.warning(f"Could not register Eloquence settings panel: {e}")
            # Continue initialization - synth will work without settings panel

        try:
            log.info("Eloquence: Starting initialization")
            _eloquence.initialize(self._onIndexReached)
            log.info("Eloquence: _eloquence.initialize completed successfully")
        except Exception as e:
            log.error(
                f"Eloquence: Failed to initialize _eloquence module: {e}", exc_info=True
            )
            raise

        try:
            voice_param = _eloquence.params.get(9)
            if voice_param is None:
                configured_voice = (
                    config.conf.get("speech", {}).get("eci", {}).get("voice", "enu")
                )
                voice_info = _eloquence.langs.get(configured_voice) or _eloquence.langs.get(
                    "enu"
                )
                voice_param = voice_info[0] if voice_info else 65536
            self._update_voice_state(voice_param, update_default=True)
            # Initialize _rate first before setting the rate property
            self._rate = self._percentToParam(50, minRate, maxRate)
            self.rate = 50
            self.variant = "1"
            self._pause_mode = 0
            log.info("Eloquence: Initialization completed successfully")
        except Exception as e:
            log.error(
                f"Eloquence: Failed during voice/parameter setup: {e}", exc_info=True
            )
            raise

    def terminate(self):
        # Safe settings panel removal - won't crash if it was never registered
        try:
            if hasattr(gui.settingsDialogs, "NVDASettingsDialog"):
                if hasattr(gui.settingsDialogs.NVDASettingsDialog, "categoryClasses"):
                    gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(
                        EloquenceSettingsPanel
                    )
        except (ValueError, AttributeError) as e:
            log.debug(f"Settings panel already removed or never registered: {e}")
        except Exception as e:
            log.warning(f"Error removing Eloquence settings panel: {e}")

        super(SynthDriver, self).terminate()

    def combine_adjacent_strings(self, lst):
        result = []
        current_string = ""
        for item in lst:
            if isinstance(item, str):
                current_string += item
            else:
                if current_string:
                    result.append(current_string)
                    current_string = ""
                result.append(item)
        if current_string:
            result.append(current_string)
        return result

    def speak(self, speechSequence):
        last = None
        outlist = []
        pending_indexes = []
        queued_speech = False

        # Reset prosody to baseline at the start of each utterance to prevent
        # state leaks from previous speech sequences (issue #59).
        for pr in (_eloquence.rate, _eloquence.pitch, _eloquence.vlm):
            outlist.append((_eloquence.cmdProsody, (pr, 1, 0)))

        # IBMTTS Logic: Combine strings before processing regex
        speechSequence = self.combine_adjacent_strings(speechSequence)

        for item in speechSequence:
            if isinstance(item, str):
                s = str(item)
                s = self.xspeakText(s)
                outlist.append((_eloquence.speak, (s,)))
                last = s
                queued_speech = True
            elif isinstance(item, IndexCommand):
                pending_indexes.append(item.index)
                outlist.append((_eloquence.index, (item.index,)))
            elif isinstance(item, BreakCommand):
                # Eloquence doesn't respect delay time in milliseconds.
                # Therefor we need to adjust waiting time depending on curernt speech rate
                # The following table of adjustments has been measured empirically
                # Then we do linear approximation
                coefficients = {
                    10: 1,
                    43: 2,
                    60: 3,
                    75: 4,
                    85: 5,
                }
                ck = sorted(coefficients.keys())
                if self.rate <= ck[0]:
                    factor = coefficients[ck[0]]
                elif self.rate >= ck[-1]:
                    factor = coefficients[ck[-1]]
                elif self.rate in ck:
                    factor = coefficients[ck[0]]
                else:
                    li = [index for index, r in enumerate(ck) if r < self.rate][-1]
                    ri = li + 1
                    ra = ck[li]
                    rb = ck[ri]
                    factor = 1.0 * coefficients[ra] + (
                        coefficients[rb] - coefficients[ra]
                    ) * (self.rate - ra) / (rb - ra)
                pFactor = factor * item.time
                pFactor = int(pFactor)
                outlist.append((_eloquence.speak, (f"`p{pFactor}.",)))
                queued_speech = True
            elif isinstance(item, LangChangeCommand):
                voice_id = self._resolve_voice_for_language(item.lang)
                if voice_id is None:
                    log.debug("No Eloquence voice mapped for language '%s'", item.lang)
                    continue
                voice_str = str(voice_id)
                if voice_str == self.curvoice:
                    if item.lang is None:
                        self._languageOverrideActive = False
                    continue
                try:
                    queued_voice = int(voice_id)
                except (TypeError, ValueError):
                    log.debug(
                        "Skipping language change for '%s': invalid voice id %r",
                        item.lang,
                        voice_id,
                    )
                    continue
                outlist.append((_eloquence.set_voice, (queued_voice,)))
                self._update_voice_state(queued_voice, update_default=item.lang is None)
            elif type(item) in self.PROSODY_ATTRS:
                pr = self.PROSODY_ATTRS[type(item)]
                # Use the raw _offset/_multiplier values directly, NOT the
                # computed properties.  NVDA guarantees that only one of them
                # is specified (they are mutually exclusive).  The computed
                # .multiplier property already folds offset into a ratio
                # using the *current* defaultValue, so passing both would
                # double-count the change.  Raw values are stable constants
                # that do not depend on defaultValue and are safe to apply
                # later in the worker thread against the live base pitch.
                raw_offset = getattr(item, "_offset", 0)
                raw_multiplier = getattr(item, "_multiplier", 1)
                outlist.append(
                    (
                        _eloquence.cmdProsody,
                        (pr, raw_multiplier, raw_offset),
                    )
                )
        if not queued_speech:
            # No speech queued. Ensure any state changes apply and emit indexes immediately
            # so sayAll can advance even when there's nothing to speak.
            for func, args in outlist:
                if func is _eloquence.index:
                    continue
                try:
                    func(*args)
                except Exception:
                    log.exception("Synthesis command failed")
            for index in pending_indexes:
                synthIndexReached.notify(synth=self, index=index)
            synthDoneSpeaking.notify(synth=self)
            return

        # Trailing Pause Logic from IBMTTS:
        if last is not None and not last.rstrip()[-1] in punctuation:
            # Mode 0 uses p0 for legacy speed performance
            # Mode 1 and 2 use p1 for standard modern speed
            p_val = "0" if self._pause_mode == 0 else "1"
            outlist.append((_eloquence.speak, (f"`p{p_val} ",)))

        outlist.append((_eloquence.index, (0xFFFF,)))
        outlist.append((_eloquence.synth, ()))
        seq = _eloquence._client._sequence
        _eloquence.synth_queue.put((outlist, seq))
        _eloquence.process()

    def xspeakText(self, text, should_pause=False):
        if _eloquence.params[9] == 65536 or _eloquence.params[9] == 65537:
            text = resub(english_fixes, text)
        if _eloquence.params[9] == 131072 or _eloquence.params[9] == 131073:
            text = resub(spanish_fixes, text)
        if _eloquence.params[9] in (196609, 196608):
            text = resub(french_fixes, text)
        if _eloquence.params[9] in ("deu", 262144):
            text = resub(german_fixes, text)
        # this converts to ansi for anticrash. If this breaks with foreign langs, we can remove it.
        # text = text.encode('mbcs')
        # Don't normalize text for Asian languages - they have multi-byte characters
        if _eloquence.params[9] not in (
            393216,
            524288,
            655360,
        ):  # Not Chinese, Japanese, or Korean
            text = normalizeText(text)
        if not self._backquoteVoiceTags:
            text = text.replace("`", " ")
        text = "`vv%d %s" % (
            self.getVParam(_eloquence.vlm),
            text,
        )  # no embedded commands

        # IBMTTS Regex Injection Logic for dynamic pausing:
        if self._pause_mode == 0:
            # Mode 0 (Do not shorten) maps punctuation to p0 for legacy snappy performance.
            text = pause_re.sub(r"\1 `p0\2\3\4", text)
        elif self._pause_mode == 2:
            # Mode 2 (Shorten all pauses) maps punctuation to p1 for consistent modern shortening.
            text = pause_re.sub(r"\1 `p1\2\3\4", text)

        text = time_re.sub(r"\1:\2 \3", text)
        if self._ABRDICT:
            text = "`da1 " + text
        else:
            text = "`da0 " + text
        if self._phrasePrediction:
            text = "`pp1 " + text
        else:
            text = "`pp0 " + text
        # if two strings are sent separately, pause between them. This might fix some of the audio issues we're having.
        if should_pause:
            p_val = "0" if self._pause_mode == 0 else "1"
            text = text + f" `p{p_val}."
        return text
        #  _eloquence.speak(text, index)

    # def cancel(self):
    #  self.dll.eciStop(self.handle)

    def pause(self, switch):
        _eloquence.pause(switch)
        #  self.dll.eciPause(self.handle,switch)

    # Pause Mode Definitions:
    # 0: Injects p0 at all punctuation for Legacy Speed.
    # 1: Standard timing with a p1 pause at the end of speech blocks only.
    # 2: Injects p1 at all punctuation for consistent Modern Shortening.
    _pauseModes = {
        "0": StringParameterInfo("0", "Do not shorten"),
        "1": StringParameterInfo("1", "Shorten at end only"),
        "2": StringParameterInfo("2", "Shorten all pauses"),
    }

    def _get_availablePausemodes(self):
        return self._pauseModes

    def _set_pauseMode(self, val):
        self._pause_mode = int(val)

    def _get_pauseMode(self):
        return str(self._pause_mode)

    _backquoteVoiceTags = False
    _ABRDICT = False
    _phrasePrediction = False

    def _get_backquoteVoiceTags(self):
        return self._backquoteVoiceTags

    def _set_backquoteVoiceTags(self, enable):
        if enable == self._backquoteVoiceTags:
            return
        self._backquoteVoiceTags = enable

    def _get_ABRDICT(self):
        return self._ABRDICT

    def _set_ABRDICT(self, enable):
        if enable == self._ABRDICT:
            return
        self._ABRDICT = enable

    def _get_phrasePrediction(self):
        return self._phrasePrediction

    def _set_phrasePrediction(self, enable):
        if enable == self._phrasePrediction:
            return
        self._phrasePrediction = enable

    def _get_rate(self):
        return self._paramToPercent(self.getVParam(_eloquence.rate), minRate, maxRate)

    def _set_rate(self, vl):
        self._rate = self._percentToParam(vl, minRate, maxRate)
        self.setVParam(_eloquence.rate, self._percentToParam(vl, minRate, maxRate))

    def _get_pitch(self):
        return self.getVParam(_eloquence.pitch)

    def _set_pitch(self, vl):
        self.setVParam(_eloquence.pitch, vl)

    def _get_volume(self):
        return self.getVParam(_eloquence.vlm)

    def _set_volume(self, vl):
        self.setVParam(_eloquence.vlm, int(vl))

    def _set_inflection(self, vl):
        vl = int(vl)
        self.setVParam(_eloquence.fluctuation, vl)

    def _get_inflection(self):
        return self.getVParam(_eloquence.fluctuation)

    def _set_hsz(self, vl):
        vl = int(vl)
        self.setVParam(_eloquence.hsz, vl)

    def _get_hsz(self):
        return self.getVParam(_eloquence.hsz)

    def _set_rgh(self, vl):
        vl = int(vl)
        self.setVParam(_eloquence.rgh, vl)

    def _get_rgh(self):
        return self.getVParam(_eloquence.rgh)

    def _set_bth(self, vl):
        vl = int(vl)
        self.setVParam(_eloquence.bth, vl)

    def _get_bth(self):
        return self.getVParam(_eloquence.bth)

    def _getAvailableVoices(self):
        o = OrderedDict()
        for name in os.listdir(_eloquence.eciPath[:-8]):
            if not name.lower().endswith(".syn"):
                continue
            voice_code = name.lower()[:-4]
            info = _eloquence.langs[voice_code]
            language = VOICE_BCP47.get(voice_code)
            o[str(info[0])] = synthDriverHandler.VoiceInfo(
                str(info[0]), info[1], language
            )
        return o

    def _get_voice(self):
        return str(_eloquence.params[9])

    def _set_voice(self, vl):
        _eloquence.set_voice(vl)
        self._update_voice_state(vl, update_default=True)

    def _update_voice_state(self, voice_id, update_default):
        voice_str = str(voice_id)
        try:
            _eloquence.params[9] = int(voice_str)
        except (TypeError, ValueError):
            log.debug("Unable to coerce Eloquence voice id '%s' to int", voice_id)
        if update_default or not getattr(self, "_defaultVoice", None):
            self._defaultVoice = voice_str
        self.curvoice = voice_str
        current_default = getattr(self, "_defaultVoice", None)
        self._languageOverrideActive = (
            (not update_default)
            and current_default is not None
            and voice_str != current_default
        )

    def _resolve_voice_for_language(self, language):
        if not language:
            return getattr(self, "_defaultVoice", None)
        normalized = language.lower().replace("_", "-")
        voice_id = LANGUAGE_TO_VOICE_ID.get(normalized)
        if voice_id:
            return voice_id
        primary, _, region = normalized.partition("-")
        default_voice = getattr(self, "_defaultVoice", None)
        default_lang = VOICE_ID_TO_BCP47.get(default_voice) if default_voice else None
        if default_lang:
            default_primary, _, default_region = default_lang.lower().partition("-")
            if default_primary == primary and (not region or default_region == region):
                return default_voice
        candidates = PRIMARY_LANGUAGE_TO_VOICE_IDS.get(primary, [])
        if not candidates:
            return None
        if region:
            for candidate in candidates:
                candidate_tag = VOICE_ID_TO_BCP47.get(candidate)
                if not candidate_tag:
                    continue
                cand_primary, _, cand_region = candidate_tag.lower().partition("-")
                if cand_primary == primary and cand_region == region:
                    return candidate
            if primary == "es":
                for candidate in candidates:
                    candidate_tag = VOICE_ID_TO_BCP47.get(candidate)
                    if candidate_tag and candidate_tag.lower().endswith("-419"):
                        return candidate
        if default_lang and default_lang.lower().partition("-")[0] == primary:
            return default_voice
        return candidates[0]

    def getVParam(self, pr):
        return _eloquence.getVParam(pr)

    def setVParam(self, pr, vl):
        _eloquence.setVParam(pr, vl)

    def _get_lastIndex(self):
        # fix?
        return _eloquence.lastindex

    def cancel(self):
        _eloquence.stop()

    def _getAvailableVariants(self):
        global variants
        return OrderedDict(
            (str(id), synthDriverHandler.VoiceInfo(str(id), name))
            for id, name in variants.items()
        )

    def _set_variant(self, v):
        global variants
        self._variant = v if int(v) in variants else "1"
        _eloquence.setVariant(int(v))
        self.setVParam(_eloquence.rate, self._rate)
        #  if 'eloquence' in config.conf['speech']:
        #   config.conf['speech']['eloquence']['pitch'] = self.pitch

    def _get_variant(self):
        return self._variant

    def _onIndexReached(self, index):
        if index is not None:
            synthIndexReached.notify(synth=self, index=index)
        else:
            synthDoneSpeaking.notify(synth=self)
