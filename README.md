# eloquence_threshold
Eloquence synthesizer NVDA add-on compatible with threshold versions of NVDA (2019.3 and later). Supports Python 3 and new NVDA speech framework.
[Download eloquence](https://github.com/pumper42nickel/eloquence_threshold/releases/latest/download/eloquence.nvda-addon)

## 64-bit support

As NVDA migrates to a 64-bit runtime the Eloquence synthesizer DLL must be
loaded from a 32-bit process.  This add-on now launches a dedicated helper
process that hosts the original 32-bit DLL and streams synthesized audio back
to NVDA using a lightweight RPC channel.  The integration is transparent to the
user—no additional Python installation or manual steps are required.

For development scenarios where the prebuilt helper executable is unavailable
the `ELOQUENCE_HOST_COMMAND` environment variable can be set to the command that
launches a compatible 32-bit Python interpreter with `host_eloquence32.py`.

### Building the 32-bit helper executable

The helper must be compiled with a 32-bit Python toolchain so that it can load
the original 32-bit Eloquence DLL.  The project uses [PyInstaller] to bundle the
script into a standalone executable that the add-on will launch automatically.

1. Install a 32-bit build of Python 3.8 or newer on Windows (for example,
   `python-3.11.6.exe` from <https://www.python.org/downloads/windows/> – make
   sure to select the *32-bit* installer).
2. Install PyInstaller into that environment:

   ```cmd
   py -3.11-32 -m pip install pyinstaller
   ```

3. From the repository root run PyInstaller to create the executable.  The
   `--noconsole` flag keeps the helper hidden when NVDA launches it and
   `--name` ensures that the output file matches what `_eloquence.py` expects.

   ```cmd
   py -3.11-32 -m PyInstaller --onefile --noconsole --name eloquence_host32 host_eloquence32.py
   ```

   On success the built helper will be written to `dist\eloquence_host32.exe`.

4. Copy `dist\eloquence_host32.exe` into the add-on package next to
   `_eloquence.py` before running `build.py`/`build.cmd` to assemble the
   `.nvda-addon` archive.

[PyInstaller]: https://pyinstaller.org/
