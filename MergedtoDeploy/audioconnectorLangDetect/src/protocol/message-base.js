// message-base.js
import { Uuid, SequenceNumber, Duration } from './core.js';

export const MessageBase = (Type = 'string', Parameters = {}) => ({
    version: '2',
    id: Uuid,
    type: Type,
    seq: SequenceNumber,
    parameters: Parameters,
});

export const ClientMessageBase = (T = 'string', P = {}) => ({
    ...MessageBase(T, P),
    serverseq: SequenceNumber,
    position: Duration,
});

export const ServerMessageBase = (T = 'string', P = {}) => ({
    ...MessageBase(T, P),
    clientseq: SequenceNumber,
});
