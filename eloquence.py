#Copyright (C) 2009-2019 eloquence fans
#synthDrivers/eci.py
#todo: possibly add to this
import speech, tones
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
    from driverHandler import NumericDriverSetting, BooleanDriverSetting
except ImportError:
    from autoSettingsUtils.driverSetting import BooleanDriverSetting, DriverSetting, NumericDriverSetting


punctuation = ",.?:;"
punctuation = [x for x in punctuation]
from ctypes import *
import ctypes.wintypes
from ctypes import wintypes
import synthDriverHandler, os, config, re, nvwave, threading, logging,driverHandler
from synthDriverHandler import SynthDriver, VoiceInfo, synthIndexReached, synthDoneSpeaking
from synthDriverHandler import SynthDriver,VoiceInfo
from . import _eloquence
from collections import OrderedDict
import unicodedata

log = logging.getLogger(__name__)

minRate=40
maxRate=150
pause_re = re.compile(r'([a-zA-Z])([.(),:;!?])( |$)')
time_re = re.compile(r"(\d):(\d+):(\d+)")
english_fixes = {
re.compile(r'(\w+)\.([a-zA-Z]+)'): r'\1 dot \2',
re.compile(r'([a-zA-Z0-9_]+)@(\w+)'): r'\1 at \2',
#Does not occur in normal use, however if a dictionary entry contains the Mc prefix, and NVDA splits it up, the synth will crash.
re.compile(r"\b(Mc)\s+([A-Z][a-z]+)"): r"\1\2",
re.compile(r'\b(.*?)c(ae|\xe6)sur(e)?', re.I): r'\1seizur',
re.compile(r"\b(|\d+|\W+)h'(r|v)[e]", re.I): r"\1h \2e",
re.compile(r"\b(\w+[bdfhjlmnqrvz])(h[he]s)([abcdefghjklmnopqrstvwy]\w+)\b", re.I): r"\1 \2\3",
re.compile(r"\b(\w+[bdfhjlmnqrvz])(h[he]s)(iron+[degins]?)", re.I): r"\1 \2\3",
re.compile(r"(\d):(\d\d[snrt][tdh])", re.I): r"\1 \2",
re.compile(r"\b([bcdfghjklmnpqrstvwxz]+)'([bcdefghjklmnprstvwxz']+)'([drtv][aeiou]?)", re.I): r"\1 \2 \3",
re.compile(r"\b(you+)'(re)+'([drv]e?)", re.I): r"\1 \2 \3",
re.compile(r"(re|un|non|anti)cosp", re.I): r"\1kosp",
re.compile(r"(EUR[A-Z]+)(\d+)", re.I): r"\1 \2",
re.compile(r"\b(\d+|\W+|[bcdfghjklmnpqrstvwxz])?t+z[s]che", re.I): r"\1tz sche",
re.compile(r"\b(juar[aeou]s)([aeiou]{6,})", re.I): r"\1 \2"
}
french_fixes = {
re.compile(r'([a-zA-Z0-9_]+)@(\w+)'): r'\1 arobase \2',
}
spanish_fixes = {
#for emails
re.compile(r'([a-zA-Z0-9_]+)@(\w+)'): r'\1 arroba \2',
}
german_fixes = {
#Crash words
re.compile(r'dane-ben', re.I): r'dane- ben',
	re.compile(r'dage-gen', re.I): r'dage- gen',
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

variants = {1:"Reed",
2:"Shelley",
3:"Bobby",
4:"Rocko",
5:"Glen",
6:"Sandy",
7:"Grandma",
8:"Grandpa"}

def strip_accents(s):
  return ''.join(c for c in unicodedata.normalize('NFD', s)
                  if unicodedata.category(c) != 'Mn')  
                  
def normalizeText(s):
  """
  Normalizes  text by removing unicode characters.
  Tries to preserve accented characters if they fall into MBCS encoding page.
  Tries to find closest ASCII characters if accented characters cannot be represented in MBCS.
  """
  result = []
  for c in s:
   try:
    cc = c.encode('mbcs').decode('mbcs')
   except UnicodeEncodeError:
    cc = strip_accents(c)
    try:
     cc.encode('mbcs')
    except UnicodeEncodeError:
     cc = "?"
   result.append(cc)
  return "".join(result)

class SynthDriver(synthDriverHandler.SynthDriver):
 supportedSettings=(SynthDriver.VoiceSetting(), SynthDriver.VariantSetting(), SynthDriver.RateSetting(), SynthDriver.PitchSetting(),SynthDriver.InflectionSetting(),SynthDriver.VolumeSetting(), NumericDriverSetting("hsz", "Head Size"), NumericDriverSetting("rgh", "Roughness"), NumericDriverSetting("bth", "Breathiness"), BooleanDriverSetting("backquoteVoiceTags","Enable backquote voice &tags", True), BooleanDriverSetting("ABRDICT","Enable &abbreviation dictionary", False), BooleanDriverSetting("phrasePrediction","Enable phrase prediction", False))
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
 
 description='ETI-Eloquence'
 name='eloquence'
 @classmethod
 def check(cls):
  return _eloquence.eciCheck()
 def __init__(self):
  _eloquence.initialize(self._onIndexReached)
  voice_param = _eloquence.params.get(9)
  if voice_param is None:
   configured_voice = config.conf.get("speech", {}).get("eci", {}).get("voice", "enu")
   voice_info = _eloquence.langs.get(configured_voice) or _eloquence.langs.get("enu")
   voice_param = voice_info[0] if voice_info else 65536
  self._update_voice_state(voice_param, update_default=True)
  self.rate=50
  self.variant = "1"

 def speak(self,speechSequence):
  last = None
  outlist = []
  for item in speechSequence:
   if isinstance(item,str):
    s=str(item)
    s = self.xspeakText(s)
    outlist.append((_eloquence.speak, (s,)))
    last = s
   elif isinstance(item,IndexCommand):
    outlist.append((_eloquence.index, (item.index,)))
   elif isinstance(item,BreakCommand):
    # Eloquence doesn't respect delay time in milliseconds.
    # Therefor we need to adjust waiting time depending on curernt speech rate
    # The following table of adjustments has been measured empirically
    # Then we do linear approximation
    coefficients = {
        10:1,
        43:2,
        60:3,
        75: 4,
        85:5,
    }
    ck = sorted(coefficients.keys())
    if self.rate <= ck[0]:
     factor = coefficients[ck[0]]
    elif self.rate >= ck[-1]:
     factor = coefficients[ck[-1]]
    elif self.rate in ck:
     factor = coefficients[self.rate]
    else:
     li = [index for index, r in enumerate(ck) if r<self.rate][-1]
     ri = li + 1
     ra = ck[li]
     rb = ck[ri]
    factor = 1.0 * coefficients[ra] + (coefficients[rb] - coefficients[ra]) * (self.rate - ra) / (rb-ra)
    pFactor = factor*item.time
    pFactor = int(pFactor)
    outlist.append((_eloquence.speak, (f'`p{pFactor}.',)))
   elif isinstance(item,LangChangeCommand):
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
     log.debug("Skipping language change for '%s': invalid voice id %r", item.lang, voice_id)
     continue
    outlist.append((_eloquence.set_voice, (queued_voice,)))
    self._update_voice_state(queued_voice, update_default=item.lang is None)
   elif type(item) in self.PROSODY_ATTRS:
    pr = self.PROSODY_ATTRS[type(item)]
    if item.multiplier==1:
     # Revert back to defaults
     outlist.append((_eloquence.cmdProsody, (pr, None,)))
    else:
     outlist.append((_eloquence.cmdProsody, (pr, item.multiplier,)))
  if last is not None and not last.rstrip()[-1] in punctuation:
   outlist.append((_eloquence.speak, ('`p1.',)))
  outlist.append((_eloquence.index, (0xffff,)))
  outlist.append((_eloquence.synth,()))
  seq = _eloquence._client._sequence
  _eloquence.synth_queue.put((outlist, seq))
  _eloquence.process()

 def xspeakText(self,text, should_pause=False):
  # Presumably dashes are handled as symbols by NVDA symbol processing, so strip extra ones to avoid too many dashes.
  text = text.replace("-", " ")
  if _eloquence.params[9] == 65536 or _eloquence.params[9] == 65537: text = resub(english_fixes, text)
  if _eloquence.params[9] == 131072 or _eloquence.params[9] == 131073: text = resub(spanish_fixes, text)
  if _eloquence.params[9] in (196609, 196608): text = resub(french_fixes, text)
  if _eloquence.params[9] in ('deu', 262144): text = resub(german_fixes, text)
  #this converts to ansi for anticrash. If this breaks with foreign langs, we can remove it.
  #text = text.encode('mbcs')
  # Don't normalize text for Asian languages - they have multi-byte characters
  if _eloquence.params[9] not in (393216, 524288, 655360):  # Not Chinese, Japanese, or Korean
   text = normalizeText(text)
  if not self._backquoteVoiceTags:
   text=text.replace('`', ' ')
  text = "`vv%d %s" % (self.getVParam(_eloquence.vlm), text) #no embedded commands
  text = pause_re.sub(r'\1 `p1\2\3', text)
  text = time_re.sub(r'\1:\2 \3', text)
  if self._ABRDICT:
   text="`da1 "+text
  else:
   text="`da0 "+text
  if self._phrasePrediction:
   text="`pp1 "+text
  else:
   text="`pp0 "+text
  #if two strings are sent separately, pause between them. This might fix some of the audio issues we're having.
  if should_pause:
   text = text + ' `p1.'
  return text
  #  _eloquence.speak(text, index)
  
  # def cancel(self):
  #  self.dll.eciStop(self.handle)

 def pause(self,switch):
  _eloquence.pause(switch)
  #  self.dll.eciPause(self.handle,switch)

 def terminate(self):
  _eloquence.terminate()
 _backquoteVoiceTags=False
 _ABRDICT=False
 _phrasePrediction=False
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
  return self._paramToPercent(self.getVParam(_eloquence.rate),minRate,maxRate)

 def _set_rate(self,vl):
  self._rate = self._percentToParam(vl,minRate,maxRate)
  self.setVParam(_eloquence.rate,self._percentToParam(vl,minRate,maxRate))

 def _get_pitch(self):
  return self.getVParam(_eloquence.pitch)

 def _set_pitch(self,vl):
  self.setVParam(_eloquence.pitch,vl)

 def _get_volume(self):
  return self.getVParam(_eloquence.vlm)

 def _set_volume(self,vl):
  self.setVParam(_eloquence.vlm,int(vl))

 def _set_inflection(self,vl):
  vl = int(vl)
  self.setVParam(_eloquence.fluctuation,vl)

 def _get_inflection(self):
  return self.getVParam(_eloquence.fluctuation)
 def _set_hsz(self,vl):
  vl = int(vl)
  self.setVParam(_eloquence.hsz,vl)

 def _get_hsz(self):
  return self.getVParam(_eloquence.hsz)

 def _set_rgh(self,vl):
  vl = int(vl)
  self.setVParam(_eloquence.rgh,vl)

 def _get_rgh(self):
  return self.getVParam(_eloquence.rgh)

 def _set_bth(self,vl):
  vl = int(vl)
  self.setVParam(_eloquence.bth,vl)

 def _get_bth(self):
  return self.getVParam(_eloquence.bth)

 def _getAvailableVoices(self):
  o = OrderedDict()
  for name in os.listdir(_eloquence.eciPath[:-8]):
   if not name.lower().endswith('.syn'): continue
   voice_code = name.lower()[:-4]
   info = _eloquence.langs[voice_code]
   language = VOICE_BCP47.get(voice_code)
   o[str(info[0])] = synthDriverHandler.VoiceInfo(str(info[0]), info[1], language)
  return o

 def _get_voice(self):
  return str(_eloquence.params[9])
 def _set_voice(self,vl):
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
  self._languageOverrideActive = (not update_default) and current_default is not None and voice_str != current_default
 def _resolve_voice_for_language(self, language):
  if not language:
   return getattr(self, "_defaultVoice", None)
  normalized = language.lower()
  voice_id = LANGUAGE_TO_VOICE_ID.get(normalized)
  if voice_id:
   return voice_id
  primary, _, region = normalized.partition('-')
  default_voice = getattr(self, "_defaultVoice", None)
  default_lang = VOICE_ID_TO_BCP47.get(default_voice) if default_voice else None
  if default_lang:
   default_primary, _, default_region = default_lang.lower().partition('-')
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
    cand_primary, _, cand_region = candidate_tag.lower().partition('-')
    if cand_primary == primary and cand_region == region:
     return candidate
   if primary == "es":
    for candidate in candidates:
     candidate_tag = VOICE_ID_TO_BCP47.get(candidate)
     if candidate_tag and candidate_tag.lower().endswith("-419"):
      return candidate
  if default_lang and default_lang.lower().partition('-')[0] == primary:
   return default_voice
  return candidates[0]
 def getVParam(self,pr):
  return _eloquence.getVParam(pr)

 def setVParam(self, pr,vl):
  _eloquence.setVParam(pr, vl)

 def _get_lastIndex(self):
  #fix?
  return _eloquence.lastindex

 def cancel(self):
  _eloquence.stop()

 def _getAvailableVariants(self):
  
  global variants
  return OrderedDict((str(id), synthDriverHandler.VoiceInfo(str(id), name)) for id, name in variants.items())

 def _set_variant(self, v):
  global variants
  self._variant = v if int(v) in variants else "1"
  _eloquence.setVariant(int(v))
  self.setVParam(_eloquence.rate, self._rate)
  #  if 'eloquence' in config.conf['speech']:
  #   config.conf['speech']['eloquence']['pitch'] = self.pitch

 def _get_variant(self): return self._variant
 
 def _onIndexReached(self, index):
  if index is not None:
   synthIndexReached.notify(synth=self, index=index)
  else:
   synthDoneSpeaking.notify(synth=self)
 

def resub(dct, s):
 for r in dct.keys():
  s = r.sub(dct[r], s)
 return s
