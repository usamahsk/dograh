import {
    Duration,
    EmptyObject,
    JsonObject,
    JsonStringMap,
    MediaParameters,
    SequenceNumber,
    LanguageCode,
    Uuid,
} from './core.js';

import { EventEntities } from './entities.js';

import {
    DTMFMessage,
    PlaybackCompletedMessage,
    PlaybackStartedMessage
} from './voice-bots.js';

import {
    MessageBase,
    ClientMessageBase,
    ServerMessageBase
} from './message-base.js';


/*export const MessageBase = (Type = 'string', Parameters = {}) => ({
    version: '2',
    id: Uuid,
    type: Type,
    seq: SequenceNumber,
    parameters: Parameters,
});
*/
/*export const ClientMessageBase = (T = 'string', P = {}) => ({
    ...MessageBase(T, P),
    serverseq: SequenceNumber,
    position: Duration,
});

export const ServerMessageBase = (T = 'string', P = {}) => ({
    ...MessageBase(T, P),
    clientseq: SequenceNumber,
});
*/
export const CloseReason = ['end', 'error', 'disconnect', 'reconnect'];

export const CloseParameters = {
    reason: CloseReason,
};

export const ClosedParameters = {};

export const DiscardedParameters = {
    start: Duration,
    discarded: Duration,
};

export const DisconnectReason = ['completed', 'unauthorized', 'error'];

export const DisconnectParameters = {
    reason: DisconnectReason,
    info: undefined,
    outputVariables: JsonStringMap,
};

export const ErrorCode =
    400 | 405 | 408 | 409 | 413 | 415 | 429 | 500 | 503;

export const ErrorParameters = {
    code: ErrorCode,
    message: 'string',
    retryAfter: undefined,
};

export const EventParameters = {
    entities: EventEntities,
};

export const Participant = {
    id: Uuid,
    ani: 'string',
    aniName: 'string',
    dnis: 'string',
};

export const OpenParameters = {
    organizationId: Uuid,
    conversationId: Uuid,
    participant: Participant,
    media: MediaParameters,
    language: undefined,
    customConfig: JsonObject,
    inputVariables: JsonStringMap,
};

export const OpenedParameters = {
    media: MediaParameters,
    discardTo: undefined,
    startPaused: undefined,
};

export const PauseParameters = {};

export const PausedParameters = {};

export const PingParameters = {
    rtt: undefined,
};

export const PongParameters = {};

export const ReconnectParameters = {
    info: undefined,
};

export const ResumeParameters = {};

export const ResumedParameters = {
    start: Duration,
    discarded: Duration,
};

export const UpdateParameters = {
    language: undefined,
};

export const UpdatedParameters = {};

export const CloseMessage = ClientMessageBase('close', CloseParameters);

export const ClosedMessage = ServerMessageBase('closed', ClosedParameters);

export const DiscardedMessage = ClientMessageBase('discarded', DiscardedParameters);

export const DisconnectMessage = ServerMessageBase('disconnect', DisconnectParameters);

export const ErrorMessage = ClientMessageBase('error', ErrorParameters);

export const EventMessage = ServerMessageBase('event', EventParameters);

export const OpenMessage = ClientMessageBase('open', OpenParameters);

export const OpenedMessage = ServerMessageBase('opened', OpenedParameters);

export const PauseMessage = ServerMessageBase('pause', PauseParameters);

export const PausedMessage = ClientMessageBase('paused', PausedParameters);

export const PingMessage = ClientMessageBase('ping', PingParameters);

export const PongMessage = ServerMessageBase('pong', PongParameters);

export const ReconnectMessage = ServerMessageBase('reconnect', ReconnectParameters);

export const ResumeMessage = ServerMessageBase('resume', ResumeParameters);

export const ResumedMessage = ClientMessageBase('resumed', ResumedParameters);

export const UpdateMessage = ClientMessageBase('update', UpdateParameters);

export const UpdatedMessage = ServerMessageBase('updated', UpdatedParameters);

export const ClientMessage = [
    CloseMessage,
    DiscardedMessage,
    ErrorMessage,
    OpenMessage,
    PausedMessage,
    PingMessage,
    ResumedMessage,
    UpdateMessage,
    DTMFMessage,
    PlaybackStartedMessage,
    PlaybackCompletedMessage,
];

export const ServerMessage = [
    ClosedMessage,
    DisconnectMessage,
    EventMessage,
    OpenedMessage,
    PauseMessage,
    PongMessage,
    ReconnectMessage,
    ResumeMessage,
    UpdatedMessage,
];

export const Message = [...ClientMessage, ...ServerMessage];

export const ClientMessageType = ClientMessage.map(msg => msg.type);
export const ClientMessageParameters = ClientMessage.map(msg => msg.parameters);

export const ServerMessageType = ServerMessage.map(msg => msg.type);
export const ServerMessageParameters = ServerMessage.map(msg => msg.parameters);

export const MessageType = [...ClientMessageType, ...ServerMessageType];
export const MessageParameters = [...ClientMessageParameters, ...ServerMessageParameters];

export const SelectParametersForType = (T, M) => M.type === T ? M.parameters : undefined;
export const SelectMessageForType = (T, M) => M.type === T ? M : undefined;

export const MessageDispatcher = M => ({
    ...Object.fromEntries(M.map(message => [message.type, (msg) => msg])),
});