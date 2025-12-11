# ChefChat CLI Documentatie 👨‍🍳

## Inhoudsopgave

1. [Inleiding](#inleiding)
2. [De ChefChat Vibe](#de-chefchat-vibe)
3. [Installatie & Setup](#installatie--setup)
4. [Aan de slag](#aan-de-slag)
5. [Het Modus Systeem](#het-modus-systeem)
6. [REPL Interface](#repl-interface)
7. [Commando's & Keyboard Shortcuts](#commandos--keyboard-shortcuts)
8. [Configuratie](#configuratie)
9. [Easter Eggs & Leuke Features](#easter-eggs--leuke-features)
10. [Geavanceerd Gebruik](#geavanceerd-gebruik)

---

## Inleiding

ChefChat CLI is een geavanceerde, AI-aangedreven command-line interface die de wereld van professionele keukens combineert met moderne software development. Geïnspireerd door de passie, precisie en toewijding van een professionele chef-kok, biedt ChefChat een unieke en plezierige manier om met AI te interacteren tijdens je dagelijkse ontwikkelwerk.

### Wat maakt ChefChat bijzonder?

- **🍳 Unieke Kulinaire Thema**: Alles draait om de keuken - van keukenstatus tot chef's wisdom
- **⚡ Intuïtief Modus Systeem**: 5 verschillende werkmodi voor elke situatie
- **🎭 Rijke Interactie**: Plezierige easter eggs, chef Ramsay style roasts, en inspirerende quotes
- **🔧 Krachtige Tools**: Integratie met diverse development tools en AI modellen
- **🎨 Mooie UI**: Rijke terminal interface met formatting en visual feedback

---

## De ChefChat Vibe

ChefChat is meer dan alleen een tool - het is een ervaring. De hele interface is doordrenkt met de passie en precisie van een professionele keuken:

### 👨‍🍳 De Chef's Mentaliteit
- **Passie voor Perfectie**: Net zoals een chef strive voor de perfecte gerecht, streeft ChefChat naar de perfecte code
- **Precisie en Zorgvuldigheid**: Elke actie wordt zorgvuldig overwogen, net zoals in een professionele keuken
- **Teamwerk**: ChefChat werkt samen met jou als je trusted sous-chef
- **Snelle Iteratie**: Net zoals in een keuken waar alles snel moet gaan, maar wel met aandacht voor kwaliteit

### 🎭 De Persoonlijkheid
ChefChat heeft een unieke persoonlijkheid geïnspireerd door:
- **Chef Ramsay's Directheid**: Soms direct en eerlijk, maar altijd constructief
- **Professional Chef's Wisdom**: Diepgaande inzichten over teamwork, precisie en excellence
- **Keuken Humor**: Soms droog, soms scherp, maar altijd met een glimlach
- **Mentorschap**: ChefChat fungeert als je mentor die je helpt groeien als developer

### 🎨 De Atmosfeer
- **Welkom & Warmte**: Bij elke start word je verwelkomd als een gast in de keuken
- **Respect voor het Proces**: De tool respecteert je workflow en aanpak
- **Celebratie van Successen**: Successen worden gevierd, fouten worden constructief aangepakt
- **Continue Verbetering**: Net zoals een chef zijn recepten verfijnt, verbetert ChefChat continu

---

## Installatie & Setup

### Vereisten
- Python 3.12 of hoger
- pip of uv package manager
- API key voor je gewenste AI provider (Mistral, OpenAI, etc.)

### Installatie via pip
```bash
pip install mistral-vibe
```

### Installatie via uv (aanbevolen)
```bash
uv add mistral-vibe
```

### Eerste Setup
1. **Start ChefChat voor het eerst:**
   ```bash
   vibe --setup
   ```

2. **Volg de onboarding:**
   - Configureer je API keys
   - Kies je voorkeursinstellingen
   - Test de verbinding

3. **Verifieer de installatie:**
   ```bash
   vibe --help
   ```

---

## Aan de slag

### Basis Gebruik
```bash
# Start de interactieve REPL
vibe

# Start met een specifieke prompt
vibe "Help me refactor this Python function"

# Continue van laatste sessie
vibe --continue

# Resume een specifieke sessie
vibe --resume session_123
```

### Programmatisch Gebruik
```bash
# Send een prompt en exit (auto-approve)
vibe --prompt "Show me the git history" --auto-approve

# Met output format
vibe --prompt "Create a test file" --output json

# Met beperkingen
vibe --prompt "Update dependencies" --max-turns 5 --max-price 0.50
```

---

## Het Modus Systeem

ChefChat's kracht zit in het flexibele modus systeem. Elke modus heeft zijn eigen karakter en toepassingen:

### 📋 PLAN Mode - "Measure Twice, Cut Once"
**De Wijze Mentor**
- **Gebruik**: Code exploration, nieuwe codebase begrijpen, planning
- **Eigenschappen**:
  - 🔒 Read-only (geen file wijzigingen)
  - ✋ Vraagt goedkeuring voor acties
  - 📋 Creëert gedetailleerde plannen
  - 💭 Denkt hardop en legt uit

**Perfect voor:**
- Het verkennen van nieuwe codebases
- Architecture planning
- Security reviews
- Understanding complex systems

### ✋ NORMAL Mode - "Safe and Steady"
**De Professional**
- **Gebruik**: Dagelijks development werk
- **Eigenschappen**:
  - ✅ Lees operaties zijn automatisch goedgekeurd
  - ✋ Vraagt bevestiging voor schrijf operaties
  - ⚠️ Waarschuwt voor risicovolle acties
  - 🎯 Balanceert veiligheid en snelheid

**Perfect voor:**
- Dagelijkse coding taken
- Code reviews
- Feature development
- Refactoring onder begeleiding

### ⚡ AUTO Mode - "Trust and Execute"
**De Expert**
- **Gebruik**: Wanneer je vertrouwt in ChefChat's capaciteiten
- **Eigenschappen**:
  - 🤖 Alle tools worden automatisch goedgekeurd
  - ⚡ Snellere uitvoering
  - 💬 Legt nog steeds uit wat gedaan wordt
  - 🔧 Handelt proactief

**Perfect voor:**
- Repetitive tasks
- Wanneer je ChefChat goed kent
- Batch operaties
- Trusted workflows

### 🚀 YOLO Mode - "Move Fast, Ship Faster"
**De Speedrunner**
- **Gebruik**: Maximum snelheid onder deadline druk
- **Eigenschappen**:
  - 🚀 Minimale output, maximale snelheid
  - ⚡ Instant tool approval
  - 🎯 Pure efficiency
  - 🔥 Geen tijd voor uitgebreide uitleg

**Perfect voor:**
- Deadline driven development
- Quick fixes
- Prototype development
- Emergency patches

### 🏛️ ARCHITECT Mode - "Design the Cathedral"
**De Visionary**
- **Gebruik**: High-level design en architectuur
- **Eigenschappen**:
  - 🔒 Read-only design focus
  - 📐 Denkt in systemen en patronen
  - 🏗️ Creëert diagrammen en architecturen
  - 💭 Abstract en conceptueel

**Perfect voor:**
- System design
- Architecture reviews
- Technical planning
- Design discussions

### Modus Wisselen
- **Shift+Tab**: Cycle door alle modi
- **/mode**: Toon huidige modus details
- **/modes**: Toon alle beschikbare modi
- Elke modus switch toont tips en context

---

## REPL Interface

### Het Chef's Kookboek REPL
ChefChat's REPL is ontworpen als een premium keuken-ervaring:

#### 🎨 Visual Design
- **Rich UI Components**: Mooie panels, formatting, en kleuren
- **Chef's Branding**: Consistent oranje (#FF7000) keuken thema
- **Mode Indicators**: Visuele indicatoren voor elke modus
- **Progress Indicators**: Loading spinners en status updates

#### 🖥️ Layout Elementen
```
┌─────────────────────────────────────────┐
│  🍳 CHEFCHAT v1.0.5                    │
│  The Tastiest AI Agent CLI              │
├─────────────────────────────────────────┤
│                                         │
│  ⚡ AUTO › ›                            │
│  Welcome to ChefChat! How can I help?   │
│                                         │
├─────────────────────────────────────────┤
│  [Shift+Tab] Mode: AUTO | Auto-Approve  │
│  [Ctrl+C] Cancel                        │
└─────────────────────────────────────────┘
```

#### 🔄 Real-time Updates
- **Live Mode Switching**: Modus wissels worden direct zichtbaar
- **Streaming Responses**: ChefChat's antwoorden komen live binnen
- **Tool Execution Feedback**: Real-time feedback tijdens tool executie
- **Status Bar**: Live status informatie onderaan

---

## Commando's & Keyboard Shortcuts

### Keyboard Shortcuts

#### 🖱️ Basis Navigatie
- `Enter`: Verstuur bericht
- `Ctrl+J` / `Shift+Enter`: Nieuwe regel invoegen
- `Escape`: Interrupt agent of sluit dialogs
- `Ctrl+C`: Quit (of clear input als tekst aanwezig)
- `Ctrl+O`: Toggle tool output view
- `Ctrl+T`: Toggle todo view

#### 🔄 Modus Controls
- `Shift+Tab`: Cycle modi: NORMAL → AUTO → PLAN → YOLO → ARCHITECT → NORMAL...

#### 🎛️ Special Features
- `!<command>`: Direct bash command uitvoering
- `@path/to/file/`: File path autocompletion

### Slash Commands

#### 📚 Standaard Commando's
- `/help` of `/h`: Toon help bericht
- `/status`: Toon agent statistieken
- `/config` of `/cfg`: Edit config instellingen
- `/reload` of `/r`: Herlaad configuratie van disk
- `/clear` of `/reset`: Clear conversatie geschiedenis
- `/log` of `/logpath`: Toon pad naar huidige log file
- `/compact` of `/summarize`: Compact conversatie via samenvatting
- `/exit`, `/quit`, of `/q`: Exit de applicatie

#### 🍳 ChefChat Specials (Easter Eggs)
- `/chef` of `/kitchen`: 👨‍🍳 Keuken status report
- `/wisdom` of `/quote`: 🧠 Random chef wisdom
- `/roast` of `/ramsay`: 🔥 Get roasted by Chef Ramsay
- `/fortune`: 🥠 Open een fortune cookie
- `/plate` of `/present`: 🍽️ Presenteer je werk mooi
- `/stats` of `/modes`: 📊 Sessie statistieken of mode details
- `/taste` of `/review`: 👅 Quick taste test (code review)
- `/timer` of `/estimate`: ⏱️ Keuken timer (tijd schattingen)

### Command Voorbeelden
```bash
# Basis commando's
/help
/status
/config
/clear

# Easter eggs
/chef
/wisdom
/roast
/plate

# Combinaties
!ls -la
@src/components/
```

---

## Configuratie

### Configuratie Bestanden
ChefChat gebruikt verschillende configuratie bestanden:

#### 🏠 Globale Configuratie
- **Locatie**: `~/.vibe/config.toml`
- **Project Config**: `./.vibe/config.toml`
- **Environment**: `~/.vibe/.env`

#### ⚙️ Belangrijke Instellingen
```toml
# Model Configuratie
active_model = "devstral-2"
system_prompt_id = "cli"

# Interface
vim_keybindings = false
textual_theme = "textual-dark"
disable_welcome_banner_animation = false

# Performance
auto_compact_threshold = 100000
api_timeout = 720.0

# Features
enable_update_checks = true
context_warnings = true
include_model_info = true
include_project_context = true
```

#### 🔑 API Keys Setup
```bash
# Via environment variables
export MISTRAL_API_KEY="your-api-key"
export OPENAI_API_KEY="your-openai-key"

# Via .env file
echo "MISTRAL_API_KEY=your-api-key" > ~/.vibe/.env
```

#### 🔧 Provider Configuratie
```toml
[[providers]]
name = "mistral"
api_base = "https://api.mistral.ai/v1"
api_key_env_var = "MISTRAL_API_KEY"
backend = "mistral"

[[models]]
name = "mistral-vibe-cli-latest"
provider = "mistral"
alias = "devstral-2"
temperature = 0.2
```

### Command Line Opties
```bash
vibe --help
vibe --setup                    # Setup wizard
vibe --agent NAME              # Load agent config
vibe --enabled-tools "bash*"   # Enable specifieke tools
vibe --output json             # Output format
vibe --max-turns 10            # Maximum turns
vibe --max-price 1.0           # Maximum cost
```

---

## Easter Eggs & Leuke Features

### 👨‍🍳 Kitchen Status (/chef)
Een complete keuken status report met:
- **Tijd-gebaseerde groet**: Morning service, lunch rush, dinner service
- **Modus informatie**: Huidige modus, tijd in modus, modus wijzigingen
- **Session statistieken**: Tools uitgevoerd, tokens gebruikt
- **Chef's wisdom**: Random inspirerende quote

### 🧠 Chef's Wisdom (/wisdom)
Een collectie van culinair-geïnspireerde programming wijsheden:
```
🍳 **Mise en place!** Get je code georganiseerd voordat je begint met features koken.
🔪 **Een scherp mes is veiliger** — houd je tools updated en dependencies clean.
🧈 **Laag en langzaam wint** — haast je niet met die refactor, laat het sudderen.
```

### 🔥 Chef Ramsay Roasts (/roast)
Gordon Ramsay style motivational burns:
```
🔥 **LOOK AT THIS CODE!** Het is zo raw, een goede compiler zou het niet aanraken!
😤 **Deze functie is zo lang**, het heeft zijn eigen postcode nodig!
🤦 **Noem je dit error handling?** Mijn oma kon beter exceptions afhandelen, EN ZIJ IS DOOD!
```

### 🥠 Developer Fortune Cookies (/fortune)
Tech mysticism en buggy prophecies:
```
🥠 **Je volgende pull request wordt gemerged zonder comments.**
   Lucky numbers: 42, 404, 200

🥠 **Een bug waarvan je dacht dat hij gefixt was... komt terug in production.**
   Lucky numbers: 500, 503, NaN
```

### 📊 Mode Display (/modes)
Complete overzicht van alle modi met:
- **Visuele mode indicatoren**: Emojis en kleuren
- **Gebruik scenario's**: Wanneer elke modus te gebruiken
- **Permission details**: Wat elke modus kan doen
- **Tips en tricks**: Praktische adviezen per modus

### 🍽️ Plating System (/plate)
ChefChat's manier om je werk mooi te presenteren:
- **Visual formatting**: Mooie presentatie van resultaten
- **Session statistics**: Overzicht van wat bereikt is
- **Achievement tracking**: Successen worden gevierd
- **Performance metrics**: Efficiency en kwaliteit metrics

---

## Geavanceerd Gebruik

### 🔧 Tool Management
ChefChat ondersteunt een uitgebreid tool systeem:

#### 🛠️ Built-in Tools
- **File Operations**: read_file, write_file, search_replace
- **Code Analysis**: grep, list_files, find_files
- **Git Integration**: git_status, git_log, git_diff
- **Bash Commands**: shell command uitvoering
- **Todo Management**: todo_create, todo_read

#### 🔌 MCP Server Integration
```toml
[[mcp_servers]]
name = "filesystem"
transport = "stdio"
command = "mcp-server-fs"

[[mcp_servers]]
name = "git"
transport = "http"
url = "http://localhost:3000"
headers = { "Authorization" = "Bearer token" }
```

### 🎯 Advanced Configuration
```toml
# Project Context
[project_context]
max_chars = 40000
default_commit_count = 5
max_depth = 3

# Session Logging
[session_logging]
enabled = true
save_dir = "~/.vibe/logs/session"

# Tool Paths
tool_paths = [
    "~/custom-tools",
    "./project-tools"
]

# Tool Filtering
enabled_tools = ["bash*", "git*", "file_*"]
disabled_tools = ["dangerous_*"]
```

### 🔄 Session Management
```bash
# Continue laatste sessie
vibe --continue

# Resume specifieke sessie
vibe --resume morning_work

# Programmatisch gebruik met sessie
vibe --prompt "Add tests" --output streaming
```

### 📊 Monitoring & Analytics
- **Token Usage**: Real-time tracking van API kosten
- **Session Statistics**: Tools, tijd, efficiency metrics
- **Performance Monitoring**: Response times, success rates
- **Cost Management**: Budget tracking en limits

### 🔒 Security Features
- **Mode-based Permissions**: Verschillende security levels per modus
- **Read-only Protection**: PLAN en ARCHITECT modes zijn veilig
- **Command Validation**: Suspicious commands worden geblokkeerd
- **API Key Management**: Secure key storage en rotation

---

## Conclusie

ChefChat CLI is meer dan alleen een development tool - het is een complete ervaring die de precision van een professionele keuken combineert met de kracht van AI-assisted development. Of je nu een beginner bent die de kneepjes van het vak wil leren of een expert die efficiënt wil werken, ChefChat's unieke vibe en krachtige features maken het tot een onmisbare companion voor elke developer.

### 🎯 Waarom ChefChat Kiezen?

1. **Unieke Persoonlijkheid**: Een tool met karakter en humor
2. **Flexibel Modus Systeem**: Voor elke situatie de juiste modus
3. **Professionele Kwaliteit**: Gebouwd voor serieus development werk
4. **Plezierige Interactie**: Easter eggs en leuke features houden het interessant
5. **Continu Evoluerend**: Actief ontwikkeld met nieuwe features

### 🚀 Ready to Cook?

Start je journey in de ChefChat keuken:
```bash
vibe
```

**Welcome to the kitchen! 👨‍🍳**

---

*Documentatie gegenereerd op 2025-12-11 | ChefChat CLI v1.0.5-dev*

*Type `/chef` in ChefChat voor meer inspiratie!*
