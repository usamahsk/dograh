// session.js

import { v4 as uuid } from 'uuid';
import dotenv from 'dotenv';

dotenv.config();

import {
    MessageHandlerRegistry
} from '../websocket/message-handlers/message-handler-registry.js';

import {
    BotService
} from '../services/bot-service.js';

import {
    RealtimeVapi
} from '../services/RealtimeVapi.js';

export class Session {

    constructor(ws, sessionId, url) {

        this.ws = ws;

        this.clientSessionId =
            sessionId;

        this.url = url;

        // IMPORTANT
        // Stable AudioHook packet size
        this.MAXIMUM_BINARY_MESSAGE_SIZE =
            3200;

        this.disconnecting = false;

        this.closed = false;

        this.messageHandlerRegistry =
            new MessageHandlerRegistry();

        this.botService =
            new BotService();

        this.conversationId =
            undefined;

        this.lastServerSequenceNumber =
            0;

        this.lastClientSequenceNumber =
            0;

        this.inputVariables = {};

        this.selectedMedia =
            undefined;

        this.selectedBot =
            null;

        this.isAudioPlaying =
            false;

        this.client =
            null;

        this.audioQueue =
            [];

        this.processingAudio =
            false;

        this.sessionReady =
            false;

        this.utterance =
            "";

        this.utteranceHistory =
            [];
    }

    // =========================
    // CLOSE
    // =========================

    async close() {

        if (this.closed)
            return;

        try {

            this.sessionReady =
                false;

            this.audioQueue = [];

            if (this.client) {

                try {

                    this.client.close();

                    console.log(
                        'Vapi session disconnected.'
                    );

                } catch (err) {

                    console.error(
                        'Error disconnecting Vapi:',
                        err
                    );
                }
            }

            this.ws.close();

        } catch (err) {

            console.error(
                'Error during session close:',
                err
            );
        }

        this.closed = true;
    }

    // =========================
    // SETTERS
    // =========================

    setConversationId(
        conversationId
    ) {

        this.conversationId =
            conversationId;
    }

    setInputVariables(
        inputVariables
    ) {

        this.inputVariables =
            inputVariables;
    }

    setSelectedMedia(
        selectedMedia
    ) {

        this.selectedMedia =
            selectedMedia;
    }

    setIsAudioPlaying(
        isAudioPlaying
    ) {

        this.isAudioPlaying =
            isAudioPlaying;
    }

    // =========================
    // PROCESS TEXT MESSAGE
    // =========================

    processTextMessage(data) {

        if (this.closed)
            return;

        const message =
            JSON.parse(data);

        // VALIDATE SEQUENCE

        if (
            message.seq !==
            this.lastClientSequenceNumber + 1
        ) {

            console.log(
                `Invalid client sequence number: ${message.seq}`
            );

            this.sendDisconnect(
                'error',
                'Invalid client sequence number.',
                {}
            );

            return;
        }

        this.lastClientSequenceNumber =
            message.seq;

        if (
            message.serverseq >
            this.lastServerSequenceNumber
        ) {

            console.log(
                `Invalid server sequence number: ${message.serverseq}`
            );

            this.sendDisconnect(
                'error',
                'Invalid server sequence number.',
                {}
            );

            return;
        }

        if (
            message.id !==
            this.clientSessionId
        ) {

            console.log(
                `Invalid Client Session ID: ${message.id}`
            );

            this.sendDisconnect(
                'error',
                'Invalid ID specified.',
                {}
            );

            return;
        }

        const handler =
            this.messageHandlerRegistry
                .getHandler(
                    message.type
                );

        if (!handler) {

            console.log(
                `Cannot find message handler for '${message.type}'`
            );

            return;
        }

        console.log(
            "inside text_GB : " +
            JSON.stringify(message)
        );

        handler.handleMessage(
            message,
            this
        );
    }

    // =========================
    // CREATE MESSAGE
    // =========================

    createMessage(
        type,
        parameters
    ) {

        return {

            id:
                this.clientSessionId,

            version:
                '2',

            seq:
                ++this.lastServerSequenceNumber,

            clientseq:
                this.lastClientSequenceNumber,

            type,

            parameters
        };
    }

    // =========================
    // SEND JSON
    // =========================

    send(message) {

        if (
            message.type === 'event'
        ) {

            console.log(
                `Sending ${message.parameters.entities[0].type} event`
            );

        } else {

            console.log(
                `Sending ${message.type} message`
            );
        }

        console.log(
            `[${new Date().toISOString()}] *message* ${JSON.stringify(message)}`
        );

        this.ws.send(
            JSON.stringify(message)
        );
    }

    // =========================
    // SEND AUDIO
    // =========================

    sendAudio(bytes) {

        if (
            !this.sessionReady
        ) {

           // console.log(
           //     "Dropping early Vapi audio."
           // );

            return;
        }

        if (
            !this.ws ||
            this.ws.readyState !==
            this.ws.OPEN
        ) {

            return;
        }

        try {

            this.ws.send(
                bytes,
                { binary: true }
            );

        } catch (err) {

            console.error(
                "Failed to send audio:",
                err
            );
        }
    }

    // =========================
    // TRANSCRIPT
    // =========================

    sendTranscript(
        transcript,
        confidence,
        isFinal
    ) {

        const channel =
            this.selectedMedia?.channels?.[0];

        if (!channel)
            return;

        const parameters = {

            id: uuid(),

            channel,

            isFinal,

            alternatives: [
                {
                    confidence,

                    interpretations: [
                        {
                            type:
                                'normalized',

                            transcript
                        }
                    ]
                }
            ]
        };

        const transcriptEvent = {

            type:
                'transcript',

            data:
                parameters
        };

        const message =
            this.createMessage(
                'event',
                {
                    entities: [
                        transcriptEvent
                    ]
                }
            );

        this.send(message);
    }

    // =========================
    // DISCONNECT
    // =========================

    sendDisconnect(
        reason,
        info,
        outputVariables
    ) {

        this.disconnecting =
            true;

        const disconnectParameters = {

            reason,

            info,

            outputVariables
        };

        const message =
            this.createMessage(
                'disconnect',
                disconnectParameters
            );

        this.send(message);
    }

    sendClosed() {

        const message =
            this.createMessage(
                'closed',
                {}
            );

        this.send(message);
    }

    // =========================
    // INITIALIZE VAPI
    // =========================

    async initializeRealTimeClient() {

        console.log(
            "initializeRealTimeClient"
        );

        try {

            const realtime =
                new RealtimeVapi();

            const client =
                await realtime.initializeClient(
                    this.inputVariables || {}
                );

            this.client =
                realtime;

            console.log(
                "Vapi initialized successfully."
            );

            // IMPORTANT
            // Reduced stabilization delay
            // to preserve first greeting

            setTimeout(() => {

                console.log(
                    "Genesys session stabilized."
                );

                this.sessionReady =
                    true;

            }, 1000);

            client.on(
                'message',

                (data, isBinary) => {

                    if (!isBinary) {

                        try {

                            const msg =
                                JSON.parse(
                                    data.toString()
                                );

                            console.log(
                                "Vapi Text:",
                                JSON.stringify(msg)
                            );

                            // OPTIONAL TRANSCRIPT

                            if (
                                msg.type ===
                                'transcript'
                            ) {

                                this.sendTranscript(
                                    msg.transcript,
                                    1.0,
                                    true
                                );
                            }

                        } catch { }

                        return;
                    }

                   // console.log(
                   //     `Received Vapi audio: ${data.length}`
                   // );

                    // IMPORTANT
                    // Keep buffering even before ready
                    // so first greeting is preserved

                    this.audioQueue.push(
                        data
                    );

                    if (
                        !this.processingAudio
                    ) {

                        this.processAudioQueue();
                    }
                }
            );

        } catch (err) {

            console.error(
                "Failed to initialize Vapi:",
                err
            );
        }
    }

    // =========================
    // PROCESS AUDIO QUEUE
    // =========================

    async processAudioQueue() {

        this.processingAudio =
            true;

        while (
            this.audioQueue.length > 0
        ) {

            // WAIT UNTIL SESSION READY

            if (
                !this.sessionReady
            ) {

                await new Promise(
                    resolve =>
                        setTimeout(
                            resolve,
                            100
                        )
                );

                continue;
            }

            let mergedBuffer =
                Buffer.alloc(0);

            // MERGE SMALL VAPI CHUNKS

            while (
                this.audioQueue.length > 0 &&
                mergedBuffer.length <
                this.MAXIMUM_BINARY_MESSAGE_SIZE
            ) {

                const nextChunk =
                    this.audioQueue.shift();

                mergedBuffer =
                    Buffer.concat([
                        mergedBuffer,
                        nextChunk
                    ]);
            }

            // SEND SINGLE FRAME

            this.sendAudio(
                mergedBuffer
            );

            // IMPORTANT
            // THROTTLE AUDIOHOOK RATE

            await new Promise(
                resolve =>
                    setTimeout(
                        resolve,
                        200
                    )
            );
        }

        this.processingAudio =
            false;
    }

    // =========================
    // HANDLE AUDIO FROM GENESYS
    // =========================

    async handleBinaryMessage(data) {

        try {

            if (
                this.client
            ) {

                await this.client.sendAudio(
                    data
                );
            }

        } catch (error) {

            console.error(
                "Failed to send audio:",
                error
            );
        }
    }

    // =========================
    // OPTIONAL BOT CHECK
    // =========================

    checkIfBotExists() {

        return this.botService
            .getBotIfExists(
                this.url,
                this.inputVariables
            )
            .then(
                selectedBot => {

                    this.selectedBot =
                        selectedBot;

                    return (
                        this.selectedBot != null
                    );
                }
            );
    }
}