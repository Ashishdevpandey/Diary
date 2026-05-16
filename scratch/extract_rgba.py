import re

with open('/home/ashish/Documents/Diary/css/style.css', 'r') as f:
    css = f.read()

# very basic css parsing to find selectors and their rgba rules
blocks = re.findall(r'([^{]+)\{([^}]+)\}', css)
for sel, rules in blocks:
    if 'rgba' in rules:
        print(f"Selector: {sel.strip()}")
        lines = rules.split(';')
        for line in lines:
            if 'rgba' in line:
                print(f"  {line.strip()}")
