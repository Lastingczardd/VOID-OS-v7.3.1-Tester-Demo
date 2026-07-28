"""Permissioned plugin loader with integrity pinning."""
import importlib.util, json, re
from pathlib import Path
from ..kernel.paths import PLUGINS
from ..kernel.security import confined_path, safe_segment, sha256_file

_PLUGIN_ID=re.compile(r"^[A-Za-z0-9_-]{1,64}$")
class PluginManager:
    def __init__(self, cfg=None):
        self.cfg=cfg or {}; self.plugins={}; self.load_errors=[]; self.reload()
    def reload(self):
        self.plugins={}; self.load_errors=[]
        for manifest in PLUGINS.glob('*/plugin.json'):
            try:
                if manifest.parent.is_symlink(): raise PermissionError('Symlinked plugins are blocked.')
                meta=json.loads(manifest.read_text(encoding='utf-8'))
                pid=meta.get('id','')
                if not _PLUGIN_ID.fullmatch(pid) or pid != manifest.parent.name:
                    raise ValueError('Plugin id must match its safe directory name.')
                tools=meta.get('tools',[])
                if not isinstance(tools,list) or not all(isinstance(x,str) and x.isidentifier() for x in tools):
                    raise ValueError('Invalid tool allowlist.')
                meta['path']=str(manifest.parent.resolve()); self.plugins[pid]=meta
            except Exception as e: self.load_errors.append(f'{manifest}: {e}')
        return self.plugins
    def invoke(self, plugin_id, tool, args):
        safe_segment(plugin_id,'plugin id')
        if plugin_id not in self.plugins: raise KeyError(f"No plugin named '{plugin_id}' is installed.")
        meta=self.plugins[plugin_id]
        if not meta.get('enabled',False): raise PermissionError(f"Plugin '{plugin_id}' is disabled.")
        if tool not in meta.get('tools',[]): raise PermissionError(f"Tool '{tool}' is not declared by plugin '{plugin_id}'.")
        if not isinstance(args,dict): raise TypeError('Plugin arguments must be an object.')
        max_bytes=int(self.cfg.get('security',{}).get('max_plugin_args_bytes',65536))
        if len(json.dumps(args).encode('utf-8')) > max_bytes: raise ValueError('Plugin arguments exceed security limit.')
        root=Path(meta['path']); tools_path=confined_path(root,'tools.py')
        if tools_path.is_symlink() or not tools_path.is_file(): raise FileNotFoundError('Safe tools.py not found.')
        expected=meta.get('reviewed_sha256','')
        actual=sha256_file(tools_path)
        if not expected or actual != expected:
            raise PermissionError('Plugin code is not integrity-pinned. Review tools.py and update reviewed_sha256.')
        spec=importlib.util.spec_from_file_location(f'void_plugin_{plugin_id}',tools_path)
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        fn=getattr(module,tool,None)
        if not callable(fn): raise AttributeError(f"Plugin '{plugin_id}' has no callable tool '{tool}'.")
        return fn(**args)
