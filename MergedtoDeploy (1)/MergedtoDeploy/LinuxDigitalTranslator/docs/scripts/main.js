import view from './view.js';
import controller from './notifications-controller.js';
import translate from './translate-service.js';
import config from './config.js';

// Obtain a reference to the platformClient object
const platformClient = require('platformClient');
const client = platformClient.ApiClient.instance;

// API instances
const usersApi = new platformClient.UsersApi();
const conversationsApi = new platformClient.ConversationsApi();
const responseManagementApi = new platformClient.ResponseManagementApi();

let userId = '';
let agentName = 'AGENT_NAME';
let agentAlias = 'AGENT_ALIAS';
let customerName = 'CUSTOMER_NAME';
let currentConversation = null;
let currentConversationId = '';
let translationData = null;
let genesysCloudLanguage = 'en-us';
let messageType = '';
let messageIds = [];

/**
 * Callback function for 'message' and 'typing-indicator' events.
 *
 * @param {Object} data the event data
 */
const onMessage = data => {
    console.log(JSON.stringify(data));

    if (data.topicName?.toLowerCase() === 'channel.metadata') return;
    if (data.eventBody.id !== currentConversationId) return;

    if (messageType === 'chat' && data.metadata.type === 'message') {
        handleChatMessage(data);
    } else if (messageType === 'message') {
        handleAgentCustomerMessage(data);
    }
};

function handleChatMessage(data) {
    const { body: message, sender: { id: senderId } } = data.eventBody;
    const participant = currentConversation.participants.find(p => p.chats[0].id === senderId);
    const name = participant?.name ?? 'BOT';
    const purpose = participant?.purpose ?? 'agent';

    translate.translateText(message, genesysCloudLanguage, translatedData => {
        view.addChatMessage(name, translatedData.translated_text, purpose);
        translationData = translatedData;
    });
}

function handleAgentCustomerMessage(data) {
    const ended = data.eventBody.participants.find(p => p.purpose === 'customer')?.endTime;
    if (ended) {
        console.log('ending conversation');
        return;
    }

    const { messages, participantPurposes } = collectRecentMessages(data);
    const mostRecent = getMostRecentMessage(messages);

    if (mostRecent && !messageIds.includes(mostRecent.messageId)) {
        const name = mostRecent.purpose === 'customer' ? customerName : agentAlias;
        conversationsApi.getConversationsMessageMessage(data.eventBody.id, mostRecent.messageId)
            .then(messageDetail => {
                if (!messageDetail.textBody) return;
                messageIds.push(mostRecent.messageId);

                translate.translateText(messageDetail.textBody, genesysCloudLanguage, translatedData => {
                    view.addChatMessage(name, translatedData.translated_text, mostRecent.purpose);
                    translationData = translatedData;
                });
            });
    }
}

function collectRecentMessages(data) {
    const messages = [];
    const participantPurposes = [];

    data.eventBody.participants.forEach(participant => {
        if (!participant.endTime && Array.isArray(participant.messages?.[0]?.messages)) {
            const lastMsg = participant.messages[0].messages.at(-1);
            if (lastMsg) {
                messages.push(lastMsg);
                participantPurposes.push(participant.purpose);
            }
        }
    });

    return { messages, participantPurposes };
}

function getMostRecentMessage(messages) {
    let mostRecentTime = '';
    let mostRecentMsg = null;

    messages.forEach(msg => {
        if (msg.messageTime > mostRecentTime) {
            mostRecentTime = msg.messageTime;
            mostRecentMsg = {
                messageId: msg.messageId,
                messageTime: msg.messageTime,
                purpose: msg.direction === 'inbound' ? 'customer' : 'agent'
            };
        }
    });

    return mostRecentMsg;
}

/**
 *  Translate then send message to the customer
 */
function sendChat() {
    let message = document.getElementById('message-textarea').value;

    // Get the last agent participant, this also fixes an issue when an agent
    // gets reconnected and reassigned a new participant id.
    let agentsArr = currentConversation.participants.filter(p => p.purpose === 'agent');
    let agent = agentsArr[agentsArr.length - 1];
    let communicationId = '';

    if(messageType === 'chat') {
        communicationId = agent.chats[0].id;
    } else if(messageType === 'message') {
        communicationId = agent.messages[0].id;
    }

    let sourceLang;

    // Default language to english if no source_language available
    if(translationData === null) {
        sourceLang = 'en';
    } else {
        sourceLang = translationData.source_language;
    }

    // Translate text to customer's local language
    translate.translateText(message, sourceLang, function(translatedData) {
        // Wait for translate to finish before calling sendMessage
        sendMessage(translatedData.translated_text, currentConversationId, communicationId, message);
    });

    document.getElementById('message-textarea').value = '';
};

/**
 *  Send message to the customer
 */
function sendMessage(message, conversationId, communicationId, originalMessage = '') {
    console.log(message);

    if(messageType === 'chat') {
        conversationsApi.postConversationsChatCommunicationMessages(
            conversationId, communicationId,
            {
                body: message,
                bodyType: 'standard'
            }
        );
    } else if(messageType === 'message') {
        conversationsApi.postConversationsMessageCommunicationMessages(
            conversationId, communicationId,
            {
                textBody: message
            }
        ).then(result => {
            if(originalMessage) {
                // Do not double-translate message - show as agent entered it
                messageIds.push(result.id);
                view.addChatMessage(agentAlias, originalMessage, 'agent');
            }
        });
    }
}

/**
 * Show the chat messages for a conversation
 * @param {String} conversationId
 * @returns {Promise}
 */
function showChatTranscript(conversationId) {
    view.clearMessages();
    currentConversationId = conversationId;
    messageIds = [];

    conversationsApi.getConversationsMessage(conversationId)
        .then(conversation => {
            currentConversation = conversation;
            const participants = filterActiveParticipants(conversation.participants);
            const messages = getSortedMessages(participants);
            displayMessages(messages);
        });
}

function filterActiveParticipants(participants) {
    return participants.filter(p =>
        p.participantId !== 'system' &&
        p.purpose !== 'acd' &&
        p.purpose !== 'dialer' &&
        !p.endTime
    );
}

function getSortedMessages(participants) {
    return participants
        .flatMap(p => p.messages?.[0]?.messages.map(m => ({ ...m, purpose: p.purpose })) || [])
        .filter(m => m.messageId && m.messageTime)
        .sort((a, b) => a.messageTime.localeCompare(b.messageTime));
}

function displayMessages(messages) {
    messages.forEach(msg => {
        if (messageIds.includes(msg.messageId)) return;

        messageIds.push(msg.messageId);
        const name = msg.purpose === 'customer' ? customerName : agentAlias;

        translate.translateText(msg.textBody, genesysCloudLanguage, translatedData => {
            view.addChatMessage(name, translatedData.translated_text, msg.purpose);
        });
    });
}

/**
 * Set-up the channel for chat conversations
 * @param {String} conversationId
 * @returns {Promise}
 */
function setupChatChannel(conversationId) {
    return controller.createChannel()
    .then(() => {
        // Subscribe to all incoming messages
        return controller.addSubscription(
            `v2.users.${userId}.conversations`,
            onMessage)
        .then(() => {
            return controller.addSubscription(
                `v2.conversations.chats.${conversationId}.messages`,
                onMessage);
        });
    });
}

/**
 * This toggles between translator and canned response iframe
 */
function toggleIframe() {
    let label = document.getElementById('toggle-iframe').textContent;

    if(label === 'Open Chat Translator') {
        document.getElementById('toggle-iframe').textContent = 'Open Canned Responses';
        document.getElementById('agent-assist').style.display = 'block';
        document.getElementById('canned-response-container').style.display = 'none';
    } else {
        document.getElementById('toggle-iframe').textContent = 'Open Chat Translator';
        document.getElementById('agent-assist').style.display = 'none';
        document.getElementById('canned-response-container').style.display = 'block';

        // Only call getLibraries function if element does not have a child
        if(document.getElementById('libraries-container').childNodes.length === 0) getLibraries();
    }
}

/** --------------------------
 *  CANNED RESPONSE FUNCTIONS
 * ------------------------ */
/**
 * Get all libraries in the org
 */
function getLibraries() {
    return responseManagementApi.getResponsemanagementLibraries()
    .then(libraries => {
        libraries.entities.forEach(library => {
            getResponses(library.id, library.name);
        });
    });
}

/**
 * Get all responses of each library
 * @param {String} libraryId
 * @param {String} libraryName
 */
function getResponses(libraryId, libraryName) {
    return responseManagementApi.getResponsemanagementResponses(libraryId)
    .then(responses => {
        view.displayLibraries(libraryId, libraryName);

        responses.entities.forEach(response => {
            view.displayResponses(response, doResponseSubstitution);
        });
    });
}

/**
 * Search all responses in the org
 * @param {String} query
 */
function searchResponse(query) {
    return responseManagementApi.postResponsemanagementResponsesQuery({ queryPhrase: query })
    .then(responses => {
        responses.results.entities.forEach(response => {
            view.toggleDIVs();
            view.displaySearchResults(response, doResponseSubstitution);
        });
    });
}

/**
 * Replaces the dynamic variables in canned responses with appropriate
 * values. This function is used in the view when an agent clicks a response.
 * @param {String} text
 * @param {String} responseId
 */
function doResponseSubstitution(text, responseId) {
    let finalText = text;

    // Do the default substitutions first
    finalText = finalText.replace(/{{AGENT_NAME}}/g, agentName);
    finalText = finalText.replace(/{{CUSTOMER_NAME}}/g, customerName);
    finalText = finalText.replace(/{{AGENT_ALIAS}}/g, agentAlias);

    let participantData = currentConversation.participants
                            .find(p => p.purpose === 'customer').attributes;

    // Do the custom substitutions
    return responseManagementApi.getResponsemanagementResponse(responseId)
    .then(responseData => {
        let subs = responseData.substitutions;
        subs.forEach(sub => {
            let subRegex = new RegExp(`{{${sub.id}}}`, 'g');
            let val = `{{${sub.id}}}`;

            // Check if substitution exists on the participant data, if not
            // use default value
            if(participantData[sub.id]) {
                val = participantData[sub.id];
            } else {
                val = sub.defaultValue ? sub.defaultValue : val;
            }

            finalText = finalText.replace(subRegex, val);
        });

        return finalText;
    })
    .catch(e => console.error(e));
}

/** --------------------------------------------------------------
 *                       EVENT HANDLERS
 * -------------------------------------------------------------- */
document.getElementById('toggle-iframe')
    .addEventListener('click', () => toggleIframe());

document.getElementById('chat-form')
    .addEventListener('submit', () => sendChat());

document.getElementById('btn-send-message')
    .addEventListener('click', () => sendChat());

document.getElementById('message-textarea')
    .addEventListener('keypress', function(e) {
        if(e.key === 'Enter') {
            sendChat();
            if(e.preventDefault) e.preventDefault(); // prevent new line
            return false; // Just a workaround for old browsers
        }
    });

document.getElementById('find-response-btn')
    .addEventListener('click', function() {
        let query = document.getElementById('find-response').value;
        searchResponse(query);
    });

document.getElementById('find-response')
    .addEventListener('keypress', function(e) {
        if(e.key === 'Enter') {
            let query = document.getElementById('find-response').value;
            searchResponse(query);
        }
    });

document.getElementById('toggle-search')
    .addEventListener('click', () => view.toggleDIVs());

/** --------------------------------------------------------------
 *                       INITIAL SETUP
 * -------------------------------------------------------------- */
const urlParams = new URLSearchParams(window.location.search);
currentConversationId = urlParams.get('conversationid');
genesysCloudLanguage = urlParams.get('language');

client.setPersistSettings(true, 'chat-translator');
client.setEnvironment(config.genesysCloud.region);
client.loginImplicitGrant(
    config.clientID,
    config.redirectUri,
    { state: JSON.stringify({
        conversationId: currentConversationId,
        language: genesysCloudLanguage
    }) })
.then(data => {
    console.log(data);

    // Assign conversation id
    let stateData = JSON.parse(data.state);
    currentConversationId = stateData.conversationId;
    genesysCloudLanguage = stateData.language;

    // Get Details of current User
    return usersApi.getUsersMe();
}).then(userMe => {
    userId = userMe.id;
    agentName = userMe.name;
    agentAlias = agentName ? agentName : agentAlias;

    // Get current conversation
    return conversationsApi.getConversation(currentConversationId);
}).then(conv => {
    currentConversation = conv;
    let customer = conv.participants.find(p => p.purpose === 'customer');

    if(null != customer.chats && customer.chats.length > 0) {
        messageType = 'chat';
        customerName = customer.name;
    } else if(null != customer.messages && customer.messages.length > 0) {
        messageType = 'message';
        customerName = 'customer';
        if(null != customer.attributes && customer.attributes.hasOwnProperty('name')) {
            customerName = customer.attributes['name'];
        }
    }

    return setupChatChannel(currentConversationId);
}).then(() => {
    // Get current chat conversations
    return showChatTranscript(currentConversationId);
}).then(() => {
    console.log('Finished Setup');
// Error Handling
}).catch(e => console.log(e));
