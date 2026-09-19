import { ClientMessage } from '../../../protocol/message.js';
import { Session } from '../../../common/session.js';
import { MessageHandler } from '../message-handler.js';

export class PlaybackStartedMessageHandler extends MessageHandler {
    handleMessage(message, session) {
        console.log('Received a Playback Started Message.');
        session.setIsAudioPlaying(true);
    }
}
