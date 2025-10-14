# eloquence_threshold
Eloquence synthesizer NVDA add-on compatible with 64-bit NVDA. Supports Python 3 and new NVDA speech framework.

## 64-bit support

As NVDA migrates to a 64-bit runtime the Eloquence synthesizer DLL must be
loaded from a 32-bit process.  This add-on now launches a dedicated helper
process that hosts the original 32-bit DLL and streams synthesized audio back
to NVDA using a lightweight RPC channel.  The integration is transparent to the
user—no additional Python installation or manual steps are required.

For development scenarios where the prebuilt helper executable is unavailable
the `ELOQUENCE_HOST_COMMAND` environment variable can be set to the command that
launches a compatible 32-bit Python interpreter with `host_eloquence32.py`.

## Building

• have the Python Install Manager installed and working from: https://www.python.org/ftp/python/pymanager/python-manager-25.0.msix
• Install Python 3.13-32 using py install 3.13-32
• Install pyinstaller using py -3.13-32 -m pip install pyinstaller
• run build.cmd
• You should then have a fully built NVDA addon

