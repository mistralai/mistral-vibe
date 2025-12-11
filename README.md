# 👨‍🍳 ChefChat CLI - The Tastiest AI Agent CLI

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Mistral Vibe Fork](https://img.shields.io/badge/Fork-Mistral_Vibe-orange.svg)](https://github.com/mistralai/mistral-vibe)

**ChefChat CLI** is a **fork of Mistral Vibe** with significant enhancements that transform it into a premium, culinary-themed AI coding assistant. We've taken the solid foundation of Mistral Vibe and added a **comprehensive mode system**, **enhanced safety features**, **rich UI improvements**, and **delightful easter eggs** to create a truly unique development experience.

---

## 🍳 Why ChefChat? The Mistral Vibe Evolution

ChefChat builds upon Mistral Vibe's excellent foundation with these **key improvements**:

### ✨ **1. Advanced Mode System** - "The Right Tool for Every Job"
Unlike the original Mistral Vibe, ChefChat features **5 distinct operating modes** that adapt to your workflow:

- **📋 PLAN Mode** - Read-only exploration and planning
- **✋ NORMAL Mode** - Safe daily development (default)
- **⚡ AUTO Mode** - Trusted automation for experts
- **🚀 YOLO Mode** - Maximum speed for deadlines
- **🏛️ ARCHITECT Mode** - High-level design thinking

### 🔒 **2. Enhanced Safety & Permission System**
Our **ModeManager** provides granular control over tool execution:
- **Read-only modes** (PLAN, ARCHITECT) block destructive operations
- **Permission-based tool access** per mode
- **No bypasses** - strict enforcement of safety rules
- **Visual mode indicators** for clear context

### 🎨 **3. Premium REPL Interface**
Replaced the original Textual UI with a **Rich + prompt_toolkit** based interface:
- **Beautiful formatting** with culinary theme (#FF7000)
- **Real-time mode switching** with Shift+Tab
- **Enhanced visual feedback** for all operations
- **Streaming responses** with live updates

### 👨‍🍳 **4. Culinary Personality & Easter Eggs**
ChefChat brings **fun and personality** to your terminal:
- **/chef** - Kitchen status report with session stats
- **/wisdom** - Culinary-inspired programming wisdom
- **/roast** - Gordon Ramsay style motivational burns
- **/fortune** - Developer fortune cookies
- **/plate** - Beautiful work presentation

### 🔧 **5. Additional Improvements**
- **Enhanced error handling** with centralized system
- **Better project context** management
- **Improved configuration** options
- **Comprehensive testing** suite for safety features

---

## 📋 Comparison: ChefChat vs Mistral Vibe

| Feature | Mistral Vibe | ChefChat |
|---------|-------------|----------|
| **Mode System** | ❌ Single mode | ✅ 5 distinct modes |
| **Safety Features** | ❌ Basic permissions | ✅ ModeManager with strict enforcement |
| **UI Framework** | ❌ Textual | ✅ Rich + prompt_toolkit |
| **Easter Eggs** | ❌ None | ✅ /chef, /wisdom, /roast, etc. |
| **Visual Feedback** | ❌ Basic | ✅ Enhanced with culinary theme |
| **Error Handling** | ❌ Basic | ✅ Centralized system |
| **Configuration** | ✅ Good | ✅ Enhanced with mode support |

---

## 🚀 Installation

### Prerequisites
- Python 3.12 or higher
- `uv` or `pip` package manager
- API key for your preferred AI provider (Mistral, OpenAI, etc.)

### Install ChefChat

```bash
# Using uv (recommended)
uv add mistral-vibe

# Using pip
pip install mistral-vibe
```

### First Run Setup
```bash
# Start the setup wizard
vibe --setup

# Follow the onboarding to:
# 1. Configure your API keys
# 2. Set your preferences
# 3. Test the connection
```

---

## 🎯 Quick Start

```bash
# Start interactive REPL
vibe

# Start with a specific prompt
vibe "Help me refactor this Python function"

# Continue from last session
vibe --continue

# Resume a specific session
vibe --resume session_123
```

---

## 📚 Mode System Deep Dive

### 📋 PLAN Mode - "Measure Twice, Cut Once"
**The Wise Mentor** - Read-only exploration and planning
- ✅ Safe for codebase exploration
- ✅ Creates detailed plans and analysis
- ❌ No file modifications allowed
- ❌ Tool execution requires approval

**Perfect for**: Codebase exploration, architecture planning, security reviews

### ✋ NORMAL Mode - "Safe and Steady"
**The Professional** - Default mode for daily development
- ✅ Read operations automatically approved
- ✅ Write operations require confirmation
- ✅ Balances safety and efficiency
- ✅ Visual feedback for all actions

**Perfect for**: Daily coding, code reviews, feature development

### ⚡ AUTO Mode - "Trust and Execute"
**The Expert** - For when you trust ChefChat's capabilities
- ✅ All tools automatically approved
- ✅ Faster execution workflow
- ✅ Still provides explanations
- ✅ Proactive problem solving

**Perfect for**: Repetitive tasks, trusted workflows, batch operations

### 🚀 YOLO Mode - "Move Fast, Ship Faster"
**The Speedrunner** - Maximum velocity under deadline pressure
- ✅ Minimal output, maximum speed
- ✅ Instant tool approval
- ✅ Pure efficiency focus
- ❌ No time for detailed explanations

**Perfect for**: Deadline-driven development, quick fixes, prototyping

### 🏛️ ARCHITECT Mode - "Design the Cathedral"
**The Visionary** - High-level design and architecture
- ✅ Read-only design focus
- ✅ System and pattern thinking
- ✅ Creates diagrams and architectures
- ✅ Abstract and conceptual

**Perfect for**: System design, architecture reviews, technical planning

---

## 🎨 ChefChat's Culinary Experience

### 👨‍🍳 The Chef's Mental Model
ChefChat brings the **precision and passion of a professional kitchen** to software development:
- **Mise en place** - Get organized before coding
- **Sharp tools** - Keep dependencies updated
- **Low and slow** - Take time for quality refactoring
- **Teamwork** - Collaborate effectively

### 🍽️ Easter Eggs & Fun Features

```bash
# Kitchen status report
/chef

# Culinary wisdom
/wisdom

# Motivational roast
/roast

# Developer fortune
/fortune

# Beautiful presentation
/plate
```

---

## 🔧 Configuration

ChefChat uses a hierarchical configuration system:

1. **Project-level**: `./.vibe/config.toml`
2. **User-level**: `~/.vibe/config.toml`
3. **Environment variables**: `.env` files

### Example Configuration
```toml
# Model settings
active_model = "devstral-2"
system_prompt_id = "cli"

# Mode-specific settings
default_mode = "NORMAL"

# Tool permissions
[tools.bash]
permission = "ask"

[tools.read_file]
permission = "always"

# UI settings
vim_keybindings = false
textual_theme = "textual-dark"
```

---

## 🛡️ Safety Features

### Mode-Based Permissions
Each mode has strict permission rules:
- **PLAN/ARCHITECT**: Read-only, no destructive operations
- **NORMAL**: Safe defaults with confirmation prompts
- **AUTO/YOLO**: Full access with different output verbosity

### Command Validation
- Suspicious commands are blocked
- File operations are validated
- API key management is secure

---

## 📊 Advanced Features

### MCP Server Integration
ChefChat supports the **Model Context Protocol** for external tool integration:

```toml
[[mcp_servers]]
name = "filesystem"
transport = "stdio"
command = "mcp-server-fs"

[[mcp_servers]]
name = "git"
transport = "http"
url = "http://localhost:3000"
```

### Session Management
```bash
# Continue last session
vibe --continue

# Resume specific session
vibe --resume morning_work

# Programmatic usage
vibe --prompt "Add tests" --output streaming
```

---

## 🤝 Community & Contributing

ChefChat is **open source** and welcomes contributions!

### Ways to Contribute
- **Report bugs** via GitHub Issues
- **Suggest features** and improvements
- **Submit pull requests** with enhancements
- **Improve documentation**
- **Share custom agents and prompts**

### Development Setup
```bash
# Clone the repository
git clone https://github.com/your-repo/chefchat.git
cd chefchat

# Install development dependencies
uv sync

# Run tests
pytest
```

---

## 📜 License

ChefChat is licensed under the **Apache License 2.0**, maintaining compatibility with the original Mistral Vibe license.

---

## 🚀 Ready to Cook?

Start your journey in the ChefChat kitchen:

```bash
vibe
```

**Welcome to the kitchen! 👨‍🍳**

---

### 📚 Documentation

- [Complete Dutch Documentation](CHEFCHAT_CLI_DOCUMENTATIE.md)
- [Technical Architecture](docs/ARCHITECTURE.md)
- [Upstream Divergence](docs/UPSTREAM_DIVERGENCE.md)
- [Contributing Guide](CONTRIBUTING.md)

### 🔗 Links

- **Original Mistral Vibe**: [https://github.com/mistralai/mistral-vibe](https://github.com/mistralai/mistral-vibe)
- **Mistral AI**: [https://mistral.ai](https://mistral.ai)
- **Documentation**: [https://github.com/mistralai/mistral-vibe#readme](https://github.com/mistralai/mistral-vibe#readme)

---

*ChefChat CLI v1.0.5-dev | Built with ❤️ on Mistral Vibe foundation*
*Type `/chef` in ChefChat for culinary inspiration!*
