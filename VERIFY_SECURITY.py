from void_os.kernel.security import verify_core_manifest, get_or_create_api_token
ok,msg=verify_core_manifest(); print(('PASS: ' if ok else 'FAIL: ')+msg)
print('API token file created at data/secrets/api_token.txt')
get_or_create_api_token()
raise SystemExit(0 if ok else 1)
