import json
import sys

args=sys.argv
if 'list' in args: print(json.dumps({'commands':[{'name':n} for n in ['core:status','pm:list','config:get','updatedb:status','config:status']]}))
elif 'core:status' in args: print(json.dumps({'drupal-version':'10.3.0','bootstrap':'Successful','php-version':'8.3','uri':'https://fixture.test'}))
elif 'config:get' in args: print(json.dumps({'module':{'token':0},'theme':{}}))
elif 'updatedb:status' in args: print(' [success] No database updates required.',file=sys.stderr)
elif 'pm:list' in args or 'config:status' in args: print('{}')
elif args[1]=='php': print(json.dumps({'version':'8.3.0','extensions':[]}))
else: print('fixture tool 13.7.6')
