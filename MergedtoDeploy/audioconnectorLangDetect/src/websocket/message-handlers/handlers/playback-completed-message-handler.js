import { ClientMessage } from '../../../protocol/message.js';
import { Session } from '../../../common/session.js';
import { MessageHandler } from '../message-handler.js';

export class PlaybackCompletedMessageHandler extends MessageHandler {
    handleMessage(message, session) {
        console.log('Received a Playback Completed Message.');
        session.setIsAudioPlaying(false);
    }
}
