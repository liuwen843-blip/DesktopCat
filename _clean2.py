# -*- coding: utf-8 -*-
path = r"f:\job-site\index.html"
text = open(path, encoding="utf-8").read()
import re
text2 = re.sub(r"^[ \t]*if \(false\) return;\r?\n", "", text, flags=re.M)
text2 = text2.replace(" || false)", ")")
text2 = text2.replace(" || false;", ";")
text2 = text2.replace("state !== IDLE || false", "state !== IDLE")
text2 = text2.replace("state === THROWING || false", "state === THROWING")
open(path, "w", encoding="utf-8").write(text2)
print("remaining false checks:", text2.count("if (false)"))
print("remaining || false:", text2.count("|| false"))
