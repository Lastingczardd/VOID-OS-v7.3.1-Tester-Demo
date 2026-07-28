import os, sys, traceback
from pathlib import Path
log=Path(__file__).with_name('VOID_OS_STARTUP_LOG.txt')
try:
    if sys.version_info < (3,10): raise RuntimeError('Python 3.10+ required')
    import tkinter
    from void_os.kernel.config import load
    from void_os.kernel.security import verify_core_manifest
    cfg=load()
    if cfg.get('security',{}).get('verify_core_on_startup',True):
        ok,msg=verify_core_manifest()
        if not ok and os.environ.get('VOID_OS_ALLOW_MODIFIED_CORE')!='1':
            raise RuntimeError(msg+' Set VOID_OS_ALLOW_MODIFIED_CORE=1 only if you intentionally changed audited core files.')
    from launcher import ForgeApp
    ForgeApp().mainloop()
except Exception:
    log.write_text(traceback.format_exc(),encoding='utf-8'); print(log.read_text()); input('Press Enter to close...')
