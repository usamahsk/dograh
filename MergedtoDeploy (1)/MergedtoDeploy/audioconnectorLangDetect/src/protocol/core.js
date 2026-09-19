// UUID as defined by RFC#4122
// Type alias for UUID
const Uuid = String;

// Non-negative integer
// Type alias for SequenceNumber
const SequenceNumber = Number;

// JSON value types
const JsonValue = [String, Number, Boolean, null, Object, Array];

// JSON array type
const JsonArray = Array;

// JSON object type
const JsonObject = {};

// JSON string map type
const JsonStringMap = {};

// Empty object type
const EmptyObject = {};

// ISO8601 duration in seconds
const Duration = 'PT0S'; // Example value

// Media channel types
const MediaChannel = ['external', 'internal'];

// Media channels type
const MediaChannels = Array;

// Media type
const MediaType = 'audio';

// Media format types
const MediaFormat = ['PCMU', 'L16'];

// Media rate
const MediaRate = 8000;

// Media parameter type
const MediaParameter = {
    type: MediaType,
    format: MediaFormat,
    channels: MediaChannels,
    rate: MediaRate,
};

// Media parameters type
const MediaParameters = Array;

// Language code type
const LanguageCode = String;

// Event entity base type
const EventEntityBase = (type, data) => ({
    type: type,
    data: data
});

export {
    Uuid,
    SequenceNumber,
    JsonValue,
    JsonArray,
    JsonObject,
    JsonStringMap,
    EmptyObject,
    Duration,
    MediaChannel,
    MediaChannels,
    MediaType,
    MediaFormat,
    MediaRate,
    MediaParameter,
    MediaParameters,
    LanguageCode,
    EventEntityBase
};