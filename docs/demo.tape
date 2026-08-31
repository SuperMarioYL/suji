Output assets/demo.gif
Set FontSize 13
Set Width 920
Set Height 480
Set Theme "Dracula"
Set Padding 12

# Headless demo: no Mac, no Ollama — the rule-based extractor + a local-file
# 公众号 article drive the full capture → ask → edit → stale flow.
# seed_demo.py sets its own SUJI_DB/SUJI_NO_LLM; the suji verbs get SUJI_DB inline.

Type "python3 examples/seed_demo.py --reset"
Enter
Sleep 2

Type "SUJI_DB=/tmp/suji_demo.db suji ask 'Q3 营收'"
Enter
Sleep 2

Type "python3 examples/seed_demo.py --edit"
Enter
Sleep 1

Type "SUJI_DB=/tmp/suji_demo.db suji stale"
Enter
Sleep 3

Type "SUJI_DB=/tmp/suji_demo.db suji sources"
Enter
Sleep 2
