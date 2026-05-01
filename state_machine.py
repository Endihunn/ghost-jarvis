"""Finite state machine for Ghost Jarvis."""
import logging
import traceback
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Callable, Optional


class State(Enum):
    IDLE = auto()
    WAKE = auto()
    LISTENING = auto()
    PROCESSING = auto()
    SPEAKING = auto()
    STANDBY = auto()


@dataclass
class StateContext:
    user_prompt: str = ""
    ghost_response: str = ""
    session_active: bool = False


class StateMachine:
    def __init__(self):
        self._state = State.IDLE
        self._context = StateContext()
        self._callbacks: dict[State, list[Callable]] = {s: [] for s in State}
        self._transition_callbacks: list[Callable[[State, State], None]] = []

    @property
    def state(self) -> State:
        return self._state

    @property
    def context(self) -> StateContext:
        return self._context

    def on_enter(self, state: State, callback: Callable = None):
        def decorator(cb: Callable):
            self._callbacks[state].append(cb)
            return cb
        if callback is None:
            return decorator
        return decorator(callback)

    def on_transition(self, callback: Callable[[State, State], None] = None):
        def decorator(cb: Callable[[State, State], None]):
            self._transition_callbacks.append(cb)
            return cb
        if callback is None:
            return decorator
        return decorator(callback)

    def transition(self, new_state: State, **kwargs):
        old_state = self._state
        if old_state == new_state:
            return
        self._state = new_state
        for key, value in kwargs.items():
            if hasattr(self._context, key):
                setattr(self._context, key, value)
        if new_state == State.IDLE and old_state != State.IDLE:
            stack = traceback.format_stack(limit=5)
            logging.getLogger("state").debug("Transition to IDLE from %s by:\n%s", old_state.name, "".join(stack))
        for cb in self._transition_callbacks:
            try:
                cb(old_state, new_state)
            except Exception:
                logging.getLogger("state").exception(
                    "Transition callback error (%s → %s)", old_state.name, new_state.name
                )
        for cb in self._callbacks[new_state]:
            try:
                cb(self._context)
            except Exception:
                logging.getLogger("state").exception(
                    "State enter callback error for %s", new_state.name
                )

    def reset_context(self):
        self._context = StateContext()
