import {
        EventEntityBase
} from './core.js';

//import { ClientMessageBase } from './message.js';
import { ClientMessageBase } from './message-base.js';

// Begin Client messages
export const DTMFParameters = {
    digit: 'string',
};
export const DTMFMessage = ClientMessageBase('dtmf', DTMFParameters);

export const PlaybackStartedParameters = {};
export const PlaybackStartedMessage = ClientMessageBase('playback_started', PlaybackStartedParameters);

export const PlaybackCompletedParameters = {};
export const PlaybackCompletedMessage = ClientMessageBase('playback_completed', PlaybackCompletedParameters);

// Begin Server events
export const EventEntityDataBargeIn = {};
export const EventEntityBargeIn = EventEntityBase('barge_in', EventEntityDataBargeIn);

export const BotTurnDisposition = ['no_input', 'no_match', 'match'];
export const EventEntityDataBotTurnResponse = {
    disposition: BotTurnDisposition,
    text: undefined,
    confidence: undefined,
};
export const EventEntityBotTurnResponse = EventEntityBase('bot_turn_response', EventEntityDataBotTurnResponse);