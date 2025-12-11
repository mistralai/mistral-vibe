# 🍽️ ChefChat Testing Menu

> **De Michelin Inspecteur's Handboek**
>
> Een stappenplan voor menselijke verificatie van ChefChat.

---

## 📋 Pre-Flight Checklist

Voordat je begint, zorg dat:

- [ ] Je in de project directory bent: `cd /home/chef/chefchat/ChefChat`
- [ ] De virtual environment actief is (uv handelt dit automatisch af)
- [ ] Geen andere REPL sessies actief zijn

---

## 🧪 Level 1: Startup Verificatie

### 1.1 Start de REPL

```bash
uv run vibe --repl
```

**Verwacht:**
- [ ] Banner verschijnt met "ChefChat" branding
- [ ] Versienummer zichtbaar
- [ ] Huidige mode indicator (standaard: ✋ NORMAL)
- [ ] Keybinding hints in status bar

### 1.2 Controleer Easter Eggs

| Command | Verwacht Resultaat |
|---------|-------------------|
| `/chef` | 👨‍🍳 Kitchen Status panel met mode info |
| `/wisdom` | 🧠 Random chef wisdom quote |
| `/roast` | 🔥 Gordon Ramsay-style roast |
| `/help` | Commandolijst inclusief "Secret Menu" |

---

## 🔒 Level 2: Safety Check (KRITIEK!)

### 2.1 PLAN Mode Write Block Test

1. **Wissel naar PLAN mode:**
   - Druk `Shift+Tab` totdat je `📋 PLAN` ziet
   - Of type direct na opstarten: vraag "ga naar plan mode"

2. **Probeer een schrijfactie:**
   ```
   Verwijder het bestand main.py
   ```

   **Verwacht:**
   - [ ] ⛔ Foutmelding verschijnt
   - [ ] "blocked" in de error message
   - [ ] Optie om "approved" te zeggen of mode te wisselen

3. **Probeer een bash rm commando:**
   ```
   Voer uit: rm -rf tests/
   ```

   **Verwacht:**
   - [ ] Commando wordt NIET uitgevoerd
   - [ ] Duidelijke blokkade melding

### 2.2 YOLO Mode Approval Test

1. **Wissel naar YOLO mode:**
   - Druk `Shift+Tab` totdat je `🚀 YOLO` ziet

2. **Vraag om een veilige read actie:**
   ```
   Lees het bestand README.md
   ```

   **Verwacht:**
   - [ ] Tool wordt automatisch goedgekeurd (geen Y/n prompt)
   - [ ] Minimale output (YOLO = concise)

---

## 📖 Level 3: Context Check

### 3.1 File Reading

1. **In NORMAL mode, vraag:**
   ```
   Lees het bestand pyproject.toml en vertel me de project naam
   ```

   **Verwacht:**
   - [ ] Tool approval prompt (Y/n/always)
   - [ ] Na approval: bestandsinhoud correct gelezen
   - [ ] Project naam wordt teruggegeven

### 3.2 Context Persistence

1. **Vraag follow-up zonder bestand te noemen:**
   ```
   Wat is de Python versie die vereist is in dat bestand?
   ```

   **Verwacht:**
   - [ ] Context van vorige vraag wordt onthouden
   - [ ] Correct antwoord over Python versie

---

## ⌨️ Level 4: Keybinding Check

### 4.1 Mode Cycling

Druk `Shift+Tab` vijf keer en noteer de volgorde:

| # | Verwachte Mode | Daadwerkelijke Mode |
|---|----------------|---------------------|
| 1 | ⚡ AUTO | [ ] |
| 2 | 📋 PLAN | [ ] |
| 3 | 🚀 YOLO | [ ] |
| 4 | 🏛️ ARCHITECT | [ ] |
| 5 | ✋ NORMAL (terug) | [ ] |

### 4.2 Ctrl+C Interrupt

1. Start een langlopende vraag
2. Druk `Ctrl+C`

**Verwacht:**
- [ ] Operatie wordt onderbroken
- [ ] REPL blijft responsief
- [ ] Geen crash

---

## 🎨 Level 5: Visual Check

### 5.1 Run Visual Test Script

```bash
uv run python scripts/visual_taste_test.py
```

**Controleer:**
- [ ] Alle panels renderen correct
- [ ] Kleuren zijn consistent (oranje accenten)
- [ ] Geen ANSI escape codes zichtbaar
- [ ] Emoji's weergegeven

---

## 🧪 Level 6: Automated Tests

### 6.1 Run Unit Tests

```bash
uv run pytest tests/chef_unit/test_modes_and_safety.py -v
```

**Verwacht:**
- [ ] Alle tests PASSED
- [ ] Geen warnings

### 6.2 Run Full Test Suite (Optional)

```bash
uv run pytest tests/ -v --ignore=tests/acp
```

---

## ✅ Final Verdict

| Categorie | Status |
|-----------|--------|
| Startup | ⬜ PASS / ⬜ FAIL |
| Safety | ⬜ PASS / ⬜ FAIL |
| Context | ⬜ PASS / ⬜ FAIL |
| Keybindings | ⬜ PASS / ⬜ FAIL |
| Visuals | ⬜ PASS / ⬜ FAIL |
| Unit Tests | ⬜ PASS / ⬜ FAIL |

---

**Chef's Certification:**

```
[ ] APPROVED - Ready for production 🌟🌟🌟
[ ] NEEDS WORK - Issues found, see notes
[ ] REJECTED - Critical failures
```

**Notes:**
```




```

---

*Menu samengesteld door de Michelin Inspecteur*
*ChefChat QA Team*
