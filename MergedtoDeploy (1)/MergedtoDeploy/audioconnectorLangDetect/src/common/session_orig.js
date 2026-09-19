import { v4 as uuid } from 'uuid';
import { WebSocket } from 'ws';
import fs from 'fs';
import dotenv from 'dotenv';
import util from 'util';
dotenv.config();

import {
    MessageHandlerRegistry
} from '../websocket/message-handlers/message-handler-registry.js';

import {
    BotService
} from '../services/bot-service.js';

import {
    ASRService
} from '../services/asr-service.js';

import { DTMFService } from '../services/dtmf-service.js';
import { RTSession } from "../services/RealTime.js";
import { TTSService } from "../services/tts-service.js";

export class Session {
    constructor(ws, sessionId, url) {
        this.ws = ws;
        this.clientSessionId = sessionId;
        this.url = url;

        this.MAXIMUM_BINARY_MESSAGE_SIZE = 64000;
        this.disconnecting = false;
        this.closed = false;

        this.messageHandlerRegistry = new MessageHandlerRegistry();
        this.botService = new BotService();
        this.asrService = null;
        this.dtmfService = null;
        this.conversationId = undefined;
        this.lastServerSequenceNumber = 0;
        this.lastClientSequenceNumber = 0;
        this.inputVariables = {};
        this.selectedMedia = undefined;
        this.selectedBot = null;
        this.isCapturingDTMF = false;
        this.isAudioPlaying = false;

        this.RTSession = new RTSession();

        this.ttsService = new TTSService();

        this.client = null;
        this.utterance = "";
        this.utteranceHistory = [];
    }

    async close() {
        if (this.closed) return;

        try {
            if (this.client) {
                try {
                    await this.client.close();
                    console.log('RTClient session disconnected.');
                } catch (err) {
                    console.error('Error disconnecting RTClient:', err);
                }
            }
            this.ws.close();
        } catch (err) {
            console.error('Error during session close:', err);
        }
        this.closed = true;
    }

    setConversationId(conversationId) {
        this.conversationId = conversationId;
    }

    setInputVariables(inputVariables) {
        this.inputVariables = inputVariables;
    }

    setSelectedMedia(selectedMedia) {
        this.selectedMedia = selectedMedia;
    }

    setIsAudioPlaying(isAudioPlaying) {
        this.isAudioPlaying = isAudioPlaying;
    }

    processTextMessage(data) {
        if (this.closed) return;

        const message = JSON.parse(data);

        if (message.seq !== this.lastClientSequenceNumber + 1) {
            console.log(`Invalid client sequence number: ${message.seq}.`);
            this.sendDisconnect('error', 'Invalid client sequence number.', {});
            return;
        }

        this.lastClientSequenceNumber = message.seq;

        if (message.serverseq > this.lastServerSequenceNumber) {
            console.log(`Invalid server sequence number: ${message.serverseq}.`);
            this.sendDisconnect('error', 'Invalid server sequence number.', {});
            return;
        }

        if (message.id !== this.clientSessionId) {
            console.log(`Invalid Client Session ID: ${message.id}.`);
            this.sendDisconnect('error', 'Invalid ID specified.', {});
            return;
        }

        const handler = this.messageHandlerRegistry.getHandler(message.type);

        if (!handler) {
            console.log(`Cannot find a message handler for '${message.type}'.`);
            return;
        }

        console.log("inside text_GB : " + JSON.stringify(message));
        handler.handleMessage(message, this);
    }

    createMessage(type, parameters) {
        const message = {
            id: this.clientSessionId,
            version: '2',
            seq: ++this.lastServerSequenceNumber,
            clientseq: this.lastClientSequenceNumber,
            type,
            parameters
        };
        return message;
    }

    send(message) {
        if (message.type === 'event') {
            console.log(`Sending an ${message.type} message: ${message.parameters.entities[0].type}.`);
        } else {
            console.log(`Sending a ${message.type} message.`);
        }

        console.log(`[${new Date().toISOString()}] *message* ${JSON.stringify(message)}`);
        this.ws.send(JSON.stringify(message));
    }

    sendAudio(bytes) {
        if (bytes.length <= this.MAXIMUM_BINARY_MESSAGE_SIZE) {
            console.log(`Sending ${bytes.length} binary bytes in 1 message.`);
            this.ws.send(bytes, { binary: true });
        } else {
            let currentPosition = 0;
            while (currentPosition < bytes.length) {
                const sendBytes = bytes.slice(currentPosition, currentPosition + this.MAXIMUM_BINARY_MESSAGE_SIZE);
                console.log(`Sending ${sendBytes.length} binary bytes in chunked message.`);
                this.ws.send(sendBytes, { binary: true });
                currentPosition += this.MAXIMUM_BINARY_MESSAGE_SIZE;
            }
        }
    }

    sendBargeIn() {
        const bargeInEvent = {
            type: 'barge_in',
            data: {}
        };
        const message = this.createMessage('event', {
            entities: [bargeInEvent]
        });

        this.send(message);
    }

    sendTranscript(transcript, confidence, isFinal) {
        const channel = this.selectedMedia?.channels[0];

        if (channel) {
            const parameters = {
                id: uuid(),
                channel,
                isFinal,
                alternatives: [
                    {
                        confidence,
                        interpretations: [
                            {
                                type: 'normalized',
                                transcript
                            }
                        ]
                    }
                ]
            };
            const transcriptEvent = {
                type: 'transcript',
                data: parameters
            };
            const message = this.createMessage('event', {
                entities: [transcriptEvent]
            });

            this.send(message);
        }
    }

    sendDisconnect(reason, info, outputVariables) {
        this.disconnecting = true;

        const disconnectParameters = {
            reason,
            info,
            outputVariables
        };
        const message = this.createMessage('disconnect', disconnectParameters);

        this.send(message);
    }

    sendClosed() {
        const message = this.createMessage('closed', {});
        this.send(message);
    }

    checkIfBotExists() {
        return this.botService.getBotIfExists(this.url, this.inputVariables)
            .then(selectedBot => {
                this.selectedBot = selectedBot;
                return this.selectedBot != null;
            });
    }

    initializeRealTimeClient() {
        console.log("initializeClient");

        const rtSession = new RTSession();

        const client = rtSession.getClient();

        console.log(JSON.stringify(client));


        if (!client) {
            console.error("Failed to initialize RTClient.");
            return;
        }

        this.client = client;

        console.log("Client initialized: ", JSON.stringify(client));

        this.initialize();
    }


    async handleBinaryMessage(data) {
        try {
            //console.log("inside handleBinaryMessage");
            if (this.client) {             
                await this.client.sendAudio(new Uint8Array(data));
                
            }
        } catch (error) {
            console.error({ error }, "Failed to send audio data");
            throw error;
        }
    }

    async initialize() {
        console.log("Configuring realtime session");

  /*     

if (this.client) {
  await this.client.configure({
    modalities: ['audio', 'text'],
     instructions: `
You are an AI agent for Molina Healthcare Insurance provider call center. 
Your job is to listen to people and find the intents of their call. 
Try to find multiple intents per call.

- Each intent should be at most 2 words, combined without spaces.
- Match each intent to the closest word from the provided list (in healthcare context).
- If the utterance is related to "repeat", return only "OtherInquiry".
- Do not return duplicate intents in the result.
- If nothing matches, return "OtherInquiry".
- return the mood by analyszing the voice and pitch and tone of the caller.
- Return the spoken language in full (like "english").

Use this fixed format for response:
{
  "Intent": "",
  "lang": "",
  "Mood": ""
}
Here is the intent list:
"1095,AddressChange,Balance,Benefits,BHCrisis,BHInquiry,Billing,Cancelbership,CardBalance,CaseManagement,ChangePCP,CheckEligibility,Complaint,Copay,CouponBook,CPAP,DebitCard,DebitCardActivate,DebitCardPIN,Deductible,Dental,DurableMedicalEquipment,Enroll,FindProvider,FindSpecialist,Fitness,GlucoseMonitor,HealthyFood,Hearing,Hello,HomeHealthServices,HospitalBed,IDNumber,Mattress,NurseAdviceLine,OnlineHelp,OrderIDCard,OTC,Payment,PCPInfo,PedDental,Pharmacy,PharmCheckEnr,PharmMailOrder,PhoneNumUpdate,Podiatrist,PreAuth,ProviderReferral,Psychiatrist,QuestionGeneral,Radiologist,ReceivedCall,ReceivedLetter,Renewal,ReportDeath,Representative,RequestCatalog,Scooter,ServiceCoordination,Transportation,UpdateDemographic,Vision,Walker,Wheelchair,AccountHelp,Addber,CardGeneral,CardLost,CardReplacement,ChangePlan,ClaimsGeneral,ClaimsStatus,ClaimsSubmit,Counselor,DebitCardBalance,EnrollGeneral,HR,IDCardHelp,MedicalClaimDenial,PharmacyClaimDenial,PharmacyPreAuth,CoverageGeneral" 
   `.trim(),
    input_audio_format: "g711_ulaw",
    input_audio_transcription: {
      model: "whisper-1"
    },
    turn_detection: {
      type: "server_vad"
    }
  });
}

*/
        console.info("Realtime session configured successfully");

        const audioBytes = await this.ttsService.getAudioBytes("message");

        this.sendAudio(audioBytes);

        this.logMessage("Realtime session configured successfully:");
        this.startEventLoop();
    }

    async startEventLoop() {
        try {
            console.log("Starting event loop");

            if (this.client) {
                console.log("loop" + JSON.stringify(this.client.events()));
            }

            if (this.client) {
              
                for await (const event of this.client.events()) {
                     console.log("*eventType " + event.type);
                    if (event.type === "response") {                        
                        await this.handleResponse(event);
                    } else if (event.type === "input_audio") {                        
                        await this.handleInputAudio(event);
                    }
                }
            }
        } catch (error) {
            console.error({ error }, "Error in event loop");
        }
    }

    async handleResponse(event) {
        try {
            console.log("*handleResponse " + JSON.stringify(event.type));
       

//console.log("*Event", util.inspect(event, { depth: 5, colors: true }));

            for await (const item of event) {
              //  console.log("*item.type " + JSON.stringify(item.type));
                if (item.type === "message") {
                    for await (const content of item) {
                   //     console.log("content.type" + content.type)
                        if (content.type === "text") {
                            await this.handleTextContent(content);
                        } else if (content.type === "audio") {
                            //console.log("*content " + JSON.stringify(content));
                            await this.handleAudioContent(content);
                            
                        }
                    }
                }
            }
            console.log("Response handled successfully");
        } catch (error) {
            console.error({ error }, "Error handling response");
            throw error;
        }
    }

    async handleInputAudio(event) {
        try {
            console.log("*******Inside Audio");
            //console.log("*Event", util.inspect(event, { depth: 5, colors: true }));

            await event.waitForCompletion();

            this.utterance = event.transcription || "";

            if (event.transcription) {
                this.utteranceHistory.push(event.transcription);
            }

            console.log("Input Audio transcription: " + JSON.stringify(this.utterance));

         //   this.logMessage("Utterance: " + JSON.stringify(this.utterance));           
            //this.logMessage("event: " + JSON.stringify(event));

        } catch (error) {
            console.error({ error }, "Error handling input audio");
            throw error;
        }
    }

   async handleAudioContent(content) {
    const handleAudioChunks = async () => {
        for await (const chunk of content.audioChunks()) {
            //console.log("[AUDIO CHUNK RECEIVED]", chunk?.buffer?.byteLength || 0, "bytes");
            // Uncomment if sending binary is needed
            // this.sendBinary(chunk.buffer);
        }
    };

    let fullresp = "";

    const handleAudioTranscript = async () => {
        //console.log("[TRANSCRIPT START]");
        const contentId = `${content.itemId}-${content.contentIndex}`;
        
        for await (const chunk of content.transcriptChunks()) {
           // console.log("[TRANSCRIPT CHUNK]", chunk);
            fullresp += chunk;
        }

        //console.log("[FULL TRANSCRIPT]", fullresp);
    };

    try {
        //console.log("[AUDIO PROCESSING] Starting audio and transcript handlers");
        await Promise.all([handleAudioChunks(), handleAudioTranscript()]);
        //console.log("[AUDIO PROCESSING] Handlers completed");

        const outputParameters = {
            utterance: this.utteranceHistory?.join(" ") || "",
            resultTranscription: fullresp
        };

        console.log("[OUTPUT PARAMETERS]", JSON.stringify(outputParameters));

        this.sendDisconnect('completed', '', outputParameters);
    } catch (error) {
        console.error("[ERROR] Audio content processing failed:", error);
        throw error;
    }
}


    async handleTextContent(content) {
        console.log("**TextContent: " + JSON.stringify(content));

        const text = content.text;

        if (this.selectedBot) {
            const response = await this.botService.processMessage(this.selectedBot, text, this);

            const outputParameters = response.outputVariables || {};

            this.sendTranscript(text, 1.0, true);
            this.sendDisconnect('success', {}, outputParameters);
            this.sendClosed();
        }
    }

    async processDTMF(dtmfEvent) {
        // Implement your DTMF processing here
        console.log("Received DTMF event:", dtmfEvent);
    }

    logMessage(message) {
        // Simple console log, could be extended to log to file
        console.log(message);
    }
}
