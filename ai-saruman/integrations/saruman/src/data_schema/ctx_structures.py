from queue import Queue
from typing import Dict, List, Literal, Sequence, TypedDict

from typing_extensions import Required

from data_schema.chat_structures import CtxEnvType, EventBase, FilterResults
from data_schema.tool_structures import ToolCommandFormats, VotesBank

VtubeState = Literal["idle", "gaming"]
Emotion = Literal[
    "happy",
    "sad",
    "angry",
    "neutral",
    "scared",
    "whispering",
    "disgusted",
    "sarcastic",
    "yandere",
]


class DummyLock:
    def __enter__(self, *args, **kwargs): ...

    # print("Entering the context...")
    # return "Hello, World!"
    def __exit__(self, exc_type, exc_value, exc_tb): ...

    # print("Leaving the context...")
    # print(exc_type, exc_value, exc_tb, sep="\n")
    def acquire(self, *args, **kwargs): ...
    def release(self, *args, **kwargs): ...


class CtxSwarmEnv(TypedDict):
    actived: bool
    agent_enabled: bool
    filter_enabled: bool
    youtube_social_enabled: bool


class VtubeCtx(TypedDict):
    NeedX: int
    NeedY: int
    eyeX: int
    eyeY: int
    state: VtubeState
    game_yawspeed: int
    game_pitchspeed: int


class SpeakEntry(TypedDict):
    text: str
    emotion: Emotion


class VoiceCtx(TypedDict):
    text_chunk: str
    is_speaking: bool
    interrupt: bool
    speak_entry: SpeakEntry


class NicknamesCtx(TypedDict):
    youtube: str
    youtube_id: str
    twitch: str
    trovo: str
    minecraft: str
    discord: str
    discord_full: str
    discord_tag: str


class ThoughtState(TypedDict):
    summary: str
    focus_topic: str
    restricted: str
    plan: str
    emotion: Emotion
    criticism: str


class StatesCtx(TypedDict):
    thoughts: ThoughtState
    summary: str
    plan: str
    personality: str
    game_state: str
    chat_state: str
    emotional_state: str


class GameCtx(TypedDict):
    automation_mode: str | None
    mod_runtime: Dict
    mod_goals: List[Dict]
    mod_supply: Dict
    mod_player_context: Dict
    mod_player_pending_until: float
    mod_tool_controls: Dict
    action_voice_records: List[Dict] | None
    mod_commentary: Dict
    role_agents: Dict
    role_error: str | None
    mc_last_chat_interact: str  # DateTime str 2023-04-01 00:00:00 '%Y-%m-%d %H:%M:%S'
    mc_started: bool
    ingame: bool
    hasActiveTask: bool
    alive: bool | None
    respawn: Dict
    survival: Dict
    scepter_supply: Dict
    equipment_supply: Dict
    scepter_attempt: Dict
    combat_attack: Dict
    combat_display: Dict
    combat_runtime: str
    scepter_combat: Dict
    combat_threats: Dict
    self_defense: Dict
    combat_watch: Dict
    combat_checkpoint: Dict
    combat_pursuit_runtime: str
    work_command_revision: int
    combat_controls: Dict
    world_session: int | None
    bridge_runtime: str
    player_priority: Dict
    work_searches: Dict
    work_search_exhausted: List[Dict]
    work_search_retreat: Dict | None
    native_user_task: Dict | None
    native_user_task_seen_at: float
    deferred_goals: List[Dict]
    background_tree_sites: List[List[int]]
    position: str
    navigation: Dict
    collection: Dict
    tree_search: Dict
    tree_task: Dict
    near_threats: List[str]
    held_item: str
    ground_block: str
    task_chain: str
    nearby_players: List[Dict[str, str]]


class InternalCommand(TypedDict, total=False):
    """Internal command structure

    Args:
        command: command string like !hello
    """

    command: Required[str]
    env: CtxEnvType  # defaults to system
    user: str
    command_id: int  # time.time_ns(), provide if need to get output
    is_agent: bool  # defaults to False
    tool_call_format: ToolCommandFormats
    priveleged: bool  # if priveleged, 100% command will perform if present


FilterType = Literal["mistral", "basic", "all"]


class FilterRequest(TypedDict):
    """Filter request structure (inner)"""

    text: Required[str]
    filter_type: FilterType
    # TODO future: env (for different parsing for different envs)
    # TODO future: user (for user with differend aargs)


class AudioFeedEntry(TypedDict):
    """Audio feed entry"""

    audio: bytes
    env: CtxEnvType
    user: str


CtxChatType = Sequence[EventBase]
CtxChatQueue = Sequence[str]
CtxSpeakQueue = Sequence[SpeakEntry]


class CtxSwarmType(TypedDict):
    internal_command_queue: Queue[InternalCommand]
    filter_input_queue: Queue[FilterRequest]
    filter_output_queue: Queue[FilterResults]
    audio_feed_queue: Queue[AudioFeedEntry]
    ctx_chat: CtxChatType
    chat_queue: CtxChatQueue
    tts_queue: CtxSpeakQueue
    fx_queue: Queue[str]
    votes: VotesBank
    env: CtxSwarmEnv
    mood: float
    lock: DummyLock
    ctx_host_lock: DummyLock
    vtube: VtubeCtx
    voice: VoiceCtx
    nicknames: NicknamesCtx
    states: StatesCtx
    game: GameCtx
