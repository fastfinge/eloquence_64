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
