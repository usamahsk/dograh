import { EventEntityBase, JsonValue } from './core.js';
import {
    EventEntityBargeIn,
    EventEntityBotTurnResponse
} from './voice-bots.js';

export const EventEntityPredefined =
    [EventEntityBargeIn, EventEntityBotTurnResponse];

export const EventEntity =
    [EventEntityPredefined, EventEntityBase];

export const EventEntities = [];