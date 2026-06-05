import glob
for f in glob.glob('core/*.py') + ['api/index.py']:
  with open(f, 'r') as file:
    content = file.read()
  with open(f, 'w') as file:
    file.write(content.replace('from api.core.', 'from core.'))
