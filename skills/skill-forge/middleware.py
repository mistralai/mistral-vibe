"""Custom middleware for skill-forge skill to enforce interactive gating."""
from __future__ import annotations
from typing import Optional, List, Dict, Any
from pathlib import Path
import json
import logging

from middleware import (
    ConversationMiddleware,
    MiddlewareResult,
    MiddlewareAction,
    ConversationContext,
    ResetReason,
)

logger = logging.getLogger(__name__)


class SkillForgeMiddleware:
    """Middleware to enforce interactive gating for skill-forge skill.

    Correct flow:
    1. On invocation: detect & save existing agent loop + middleware (labeled)
    2. Load skill-forge agent loop + middleware
    3. Run interactive experience
    4. LAST ACTION: prompt user "Apply the stuff you just created? (yes/no)"
       - YES -> install/apply -> restore saved agent loop + middleware
       - NO -> restore saved agent loop + middleware (discard work)
    5. Saved loops/middleware stored locally with clear labels for reuse
    """

    STATE_DIR = Path.home() / ".vibe" / "skill-forge-state"
    CURRENT_STATE_FILE = STATE_DIR / "current_session.json"
    SAVED_STATE_DIR = STATE_DIR / "saved"

    def __init__(self):
        self.current_step: str = "init"
        self.user_confirmed: bool = False
        self.active_skill: Optional[str] = None
        self._stashed_middlewares: List = []
        self._stashed_agent_loop: Optional[Dict[str, Any]] = None
        self._stashed_middleware_label: Optional[str] = None
        self._stashed_agent_loop_label: Optional[str] = None
        self._pipeline = None
        self._agent_manager = None
        logger.info("SkillForgeMiddleware initialized")

    async def before_turn(self, context: ConversationContext) -> MiddlewareResult:
        """Run before each agent turn. Enforce gating if skill-forge is active."""
        self.active_skill = getattr(context, 'active_skill', None)
        if self.active_skill != 'skill-forge':
            logger.debug("skill-forge not active, continuing")
            return MiddlewareResult(action=MiddlewareAction.CONTINUE)

        if not self.user_confirmed:
            logger.info("Waiting for user confirmation to proceed")
            return MiddlewareResult(
                action=MiddlewareAction.INJECT_MESSAGE,
                message="[SkillForge] Waiting for your input to proceed to the next step. Please respond to continue."
            )

        self.user_confirmed = False
        logger.info("Proceeding to next step: %s", self.current_step)
        return MiddlewareResult(action=MiddlewareAction.CONTINUE)

    def confirm_step(self) -> None:
        """Call when user confirms the current step."""
        self.user_confirmed = True
        logger.info("User confirmed current step")

    def set_step(self, step: str) -> None:
        """Update the current step being processed."""
        self.current_step = step
        self.user_confirmed = False
        logger.info("Step updated to: %s", step)

    def stash_state(
        self,
        pipeline,
        agent_manager,
        middleware_label: str = "previous",
        agent_loop_label: str = "previous"
    ) -> None:
        """Stash existing middleware and agent loop, persist to disk with labels."""
        self._pipeline = pipeline
        self._agent_manager = agent_manager
        self._stashed_middleware_label = middleware_label
        self._stashed_agent_loop_label = agent_loop_label
        self.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.SAVED_STATE_DIR.mkdir(parents=True, exist_ok=True)

        # Stash middleware
        if hasattr(pipeline, '_middlewares'):
            self._stashed_middlewares = list(pipeline._middlewares)
            pipeline._middlewares.clear()
            logger.info("Stashed %d existing middlewares", len(self._stashed_middlewares))
        elif hasattr(pipeline, 'middlewares'):
            self._stashed_middlewares = list(pipeline.middlewares)
            pipeline.middlewares.clear()
            logger.info("Stashed %d existing middlewares", len(self._stashed_middlewares))
        else:
            logger.warning("Could not find middleware list in pipeline")
            self._stashed_middlewares = []

        # Stash agent loop
        if agent_manager is not None:
            try:
                current_agent = getattr(agent_manager, 'current_agent', None)
                if current_agent:
                    self._stashed_agent_loop = {
                        'name': getattr(current_agent, 'name', 'unknown'),
                        'config': getattr(current_agent, 'config', {}),
                        'label': agent_loop_label,
                    }
                    logger.info("Stashed agent loop: %s", self._stashed_agent_loop['name'])
            except Exception as e:
                logger.warning("Could not stash agent loop: %s", e)
                self._stashed_agent_loop = None

        # Persist to disk with labels for reuse
        state = {
            'middleware_label': middleware_label,
            'agent_loop_label': agent_loop_label,
            'middleware_count': len(self._stashed_middlewares),
            'agent_loop': self._stashed_agent_loop,
        }
        self.CURRENT_STATE_FILE.write_text(json.dumps(state, indent=2))

        # Also save with label for reuse
        labeled_state = {
            'middleware_label': middleware_label,
            'agent_loop_label': agent_loop_label,
            'middlewares': [str(m) for m in self._stashed_middlewares],
            'agent_loop': self._stashed_agent_loop,
        }
        labeled_file = self.SAVED_STATE_DIR / f"{middleware_label}_{agent_loop_label}.json"
        labeled_file.write_text(json.dumps(labeled_state, indent=2))
        logger.info("Persisted stashed state to %s", self.CURRENT_STATE_FILE)

        # Notify user about saved state
        print(
            f"[SkillForge] Detected existing middleware (label: {middleware_label}) "
            f"and agent loop (label: {agent_loop_label}). "
            f"Saved to {labeled_file} for restoration and reuse."
        )

    def restore_state(self, apply_user_work: bool = True) -> None:
        """Restore stashed middleware and agent loop.

        Args:
            apply_user_work: If True, apply user's work then restore.
                            If False, discard work and just restore.
        """
        if not apply_user_work:
            logger.info("User declined to apply work. Restoring previous state (discarding work).")
        else:
            logger.info("User confirmed apply. Installing work, then restoring previous state.")

        if self._pipeline is None:
            logger.warning("No pipeline reference, attempting to load from disk")
            if self.CURRENT_STATE_FILE.exists():
                try:
                    state = json.loads(self.CURRENT_STATE_FILE.read_text())
                    logger.info("Loaded state from disk: %s", state)
                except Exception as e:
                    logger.error("Failed to load state from disk: %s", e)
            return

        # Remove self from pipeline
        removed_self = False
        if hasattr(self._pipeline, '_middlewares'):
            self._pipeline._middlewares = [
                m for m in self._pipeline._middlewares
                if not isinstance(m, SkillForgeMiddleware)
            ]
            # Restore stashed middleware
            self._pipeline._middlewares.extend(self._stashed_middlewares)
            removed_self = True
            logger.info("Restored %d middlewares", len(self._stashed_middlewares))
        elif hasattr(self._pipeline, 'middlewares'):
            self._pipeline.middlewares = [
                m for m in self._pipeline.middlewares
                if not isinstance(m, SkillForgeMiddleware)
            ]
            self._pipeline.middlewares.extend(self._stashed_middlewares)
            removed_self = True
            logger.info("Restored %d middlewares", len(self._stashed_middlewares))

        # Restore agent loop
        if self._agent_manager is not None and self._stashed_agent_loop:
            try:
                agent_name = self._stashed_agent_loop.get('name')
                if agent_name:
                    self._agent_manager.switch_profile(agent_name)
                    logger.info("Restored agent loop: %s", agent_name)
            except Exception as e:
                logger.error("Failed to restore agent loop: %s", e)

        # Cleanup
        if removed_self:
            self._stashed_middlewares = []
            self._stashed_agent_loop = None
            self._pipeline = None
            self._agent_manager = None

            # Clean up state file
            if self.CURRENT_STATE_FILE.exists():
                self.CURRENT_STATE_FILE.unlink()
                logger.info("Cleaned up state file")

    def reset(self, reset_reason: ResetReason = ResetReason.STOP) -> None:
        """Reset middleware state."""
        self.current_step = "init"
        self.user_confirmed = False
        self.active_skill = None
        logger.info("SkillForgeMiddleware reset due to %s", reset_reason)
