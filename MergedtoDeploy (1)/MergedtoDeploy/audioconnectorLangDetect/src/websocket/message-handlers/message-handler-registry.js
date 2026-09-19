import { MessageHandler } from './message-handler.js';
import { OpenMessageHandler } from './handlers/open-message-handler.js';
import { CloseMessageHandler }  from './handlers/close-message-handler.js';
import { PingMessageHandler } from './handlers/ping-message-handler.js';
import { PlaybackStartedMessageHandler } from './handlers/playback-started-message-handler.js';
import { PlaybackCompletedMessageHandler } from './handlers/playback-completed-message-handler.js';
import { DTMFMessageHandler } from './handlers/dtmf-message-handler.js';
import { ErrorMessageHandler } from './handlers/error-message-handler.js';

export class MessageHandlerRegistry {
    constructor() {
        this.messageHandlers = new Map();

        this.messageHandlers.set('open', new OpenMessageHandler());
        this.messageHandlers.set('close', new CloseMessageHandler());
        this.messageHandlers.set('ping', new PingMessageHandler());
        this.messageHandlers.set('playback_started', new PlaybackStartedMessageHandler());
        this.messageHandlers.set('playback_completed', new PlaybackCompletedMessageHandler());
        this.messageHandlers.set('dtmf', new DTMFMessageHandler());
        this.messageHandlers.set('error', new ErrorMessageHandler());
    }

    getHandler(type) {
        return this.messageHandlers.get(type);
    }
}