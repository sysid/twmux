**Fleet Management** for AI agents.

Since you are using `workmux` (which is excellent because it combines **Git Worktrees** with **Tmux
Windows**), we can build an architecture that solves the biggest risk of parallel agents: **File
Conflicts.** If two agents edit the same file in the same directory, you get chaos. `workmux`
solves this by giving every agent its own directory (worktree) while sharing the same git history.

Here is the **"Maestro Model"**—a consistent orchestrator for high-velocity, human-in-the-loop
software development.

### The Maestro Architecture

**The Core Concepts:**

1. **The Podium (Main Session):** This is where YOU sit. You are the conductor. You don't code here; you dispatch tasks.
2. **The Pits (Parallel Worktrees):** Every task gets a dedicated `workmux` window. This creates a fresh folder (git worktree) so Agent A can mess up `main.go` without Agent B seeing it until merge time.
3. **The Protocol (Timing & Safety):** A thin wrapper script ensures agents don't "trip" over their own keystrokes.

---

### Phase 1: The Safety Layer (Solving the Timing Bug)

Before configuring the orchestrator, we must fix the "Enter" key race condition. Agents are
notorious for sending text to tmux faster than the shell can buffer it.

Create a robust wrapper script at `~/bin/safe-tmux`:

```bash
#!/bin/bash
# ~/bin/safe-tmux
# Usage: safe-tmux [session:window.pane] "command string"

TARGET=$1
CMD=$2

# 1. Send the command literally (no interpretation of $ or ")
tmux send-keys -t "$TARGET" -l "$CMD"

# 2. Wait for the shell buffer (Crucial for the "timing problem")
sleep 0.05

# 3. Send the Enter key as a hex code (More reliable than \n)
tmux send-keys -t "$TARGET" C-m

```

*Make it executable:* `chmod +x ~/bin/safe-tmux`

---

### Phase 2: The Orchestrator Config (`.workmux.yaml`)

Configure `workmux` to be the "Factory" that produces agent environments. Create or edit
`.workmux.yaml` in your project root (or `~/.workmux.yaml` for global).

This config tells workmux: *"When I start a new task, give me a split window with Claude-Code ready to go."*

```yaml
# .workmux.yaml

# 1. Naming Convention: Ensure task branches are easy to read
branch_prefix: "agent/"

# 2. The Agent Layout
panes:
  # Pane 0: The Agent's Brain
  - command: claude-code
    focus: true
  
  # Pane 1: The Agent's Body (Logs/Server)
  # We leave this generic so the agent can claim it
  - split: horizontal
    command: echo "Waiting for agent commands..."

# 3. Automation (Optional)
# You can auto-install deps in the new worktree so the agent doesn't have to
post_create:
  - npm install # or cargo build, etc.

```

---

### Phase 3: The "Conductor" Workflow

This is how you control the process professionally. You never manually copy folders or manage
sessions; you just "Dispatch" and "Merge."

#### Step 1: Dispatch (The Human Action)

You identify two tasks: "Fix the auth bug" and "Refactor the header."
Run these commands from your Podium terminal:

```bash
workmux add fix/auth-bug
workmux add refactor/header

```

* **What happens:** Workmux creates two new windows. It creates two fresh git worktrees. It installs dependencies in both. It launches `claude-code` in both.

#### Step 2: Briefing (The Handoff)

Switch to the first window (`Ctrl+b` `1`).

* **You:** "Read the auth logs in pane 1 and fix the login timeout."
* **Claude:** Starts working *in isolation*.

Switch to the second window (`Ctrl+b` `2`).

* **You:** "Refactor the header component. Use pane 1 to run the storybook."
* **Claude:** Starts working *in isolation*.

#### Step 3: The "Cockpit View" (Monitoring)

To watch everything without jumping windows, we use the **Spectator Mode**.
Add this to your `.tmux.conf`:

```bash
# Press Prefix + w to see the standard tree, 
# OR use this custom "Dashboard" to tile all active agents:
bind-key O select-layout tiled

```

Now, if you want to see all 4 agents running at once, just press `Prefix + O`.

#### Step 4: The Merge (Quality Control)

When Agent A finishes:

1. It commits the code in its own worktree.
2. You review it.
3. You run:
```bash
workmux merge fix/auth-bug

```


This automatically merges that worktree back into your main branch and cleans up the directory.

---

### Phase 4: The "System Prompt" for Claude

You need to teach Claude how to use this environment. It doesn't know it's in a `workmux` worktree unless you tell it.

**Add this to your `CLAUDE.md` or system prompt:**

> **ENVIRONMENT: PARALLEL ORCHESTRATOR**
> 1. **Isolation:** You are running in a dedicated Git Worktree. You can edit files freely; you will not conflict with other agents.
> 2. **Layout:** Your terminal is split.
> * **Pane 0 (Top):** Conversation (You are here).
> * **Pane 1 (Bottom):** Execution. Use this for running servers, tests, or logs.
> 
> 
> 3. **Execution Protocol:**
> * To run commands in the bottom pane, use: `~/bin/safe-tmux {session}:.{pane_index} "command"`
> * *Example:* `~/bin/safe-tmux $TMUX_PANE:0.1 "npm test"`
> 
> 
> 4. **Self-Correction:** If the bottom pane seems "stuck" or didn't receive the Enter key, re-send the command using the `safe-tmux` tool.
> 
> 

---

### Summary Checklist

| Component | Tool | Purpose |
| --- | --- | --- |
| **Isolation** | `workmux` | Prevents file conflicts between parallel agents. |
| **Stability** | `safe-tmux` | Fixes the "rapid-fire Enter" bug. |
| **Context** | `.workmux.yaml` | Auto-boots `claude-code` in every new task. |
| **Cleanup** | `workmux merge` | Brings the agent's work back to the human. |

**Would you like me to write a shell function that wraps `workmux add` so you can pass the initial prompt directly in one line?** (e.g., `dispatch "Fix the bug in login.ts"`)
