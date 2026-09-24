f = "app/templates/dashboard.html"
s = open(f, encoding="utf-8").read()
old = """      {% for t in top %}<tr><td>{{ t.name }}</td><td class="num">{{ t.points }}</td>
        <td class="num">{{ t.records }}</td><td class="num">{{ t.p2 }}</td></tr>{% endfor %}"""
new = """      {% for tp in top %}<tr><td>{{ tp.name }}</td><td class="num">{{ tp.points }}</td>
        <td class="num">{{ tp.records }}</td><td class="num">{{ tp.p2 }}</td></tr>{% endfor %}"""
if old in s:
    s = s.replace(old, new)
    open(f, "w", encoding="utf-8").write(s)
    print("fixed dashboard top loop t→tp")
else:
    print("PATTERN NOT FOUND")
