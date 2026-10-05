import os
import sys
if sys.stdout.encoding != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except Exception: pass
from dotenv import load_dotenv
load_dotenv('.env')

from register import admin_page, admin_login, admin_logout, require_admin

# 1. Test GET /admin without session -> displays login page
login_page_resp = admin_page(credentials=None, admin_session=None)
body = login_page_resp.body.decode('utf-8')
assert 'Admin Portal' in body
assert 'Unlock Admin Panel' in body
print('✓ GET /admin without session displays Admin Login HTML')

# 2. Test login success with 'admin' (lowercase) and 'the_fool_12'
login_res = admin_login(username='admin', password='the_fool_12')
assert login_res.status_code == 200
cookie_header = login_res.headers['set-cookie']
assert 'admin_session=' in cookie_header
print('✓ POST /admin/login succeeds and sets admin_session cookie')

# Extract token
token = cookie_header.split('admin_session=')[1].split(';')[0]

# 3. Test page access with token
dash_resp = admin_page(credentials=None, admin_session=token)
dash_body = dash_resp.body.decode('utf-8')
assert 'Registered Users' in dash_body
print('✓ GET /admin with session token displays Admin Dashboard')

# 4. Test require_admin with token
auth = require_admin(credentials=None, admin_session=token)
assert auth['valid'] is True
print('✓ require_admin accepts session token')

# 5. Test logout
logout_res = admin_logout(admin_session=token)
assert logout_res.status_code == 302
assert 'Max-Age=0' in logout_res.headers['set-cookie'] or 'deleted' in logout_res.headers['set-cookie']
print('✓ GET /admin/logout clears session cookie')

print('\n🎉 DIRECT ROUTE & AUTH TEST PASSED 100%!')
