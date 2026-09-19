import {
    Duration,
    EventEntityBase,
    MediaChannel,
    LanguageCode,
    Uuid,
} from './core.js';

/**
 * @typedef {Object} TranscriptToken
 * @property {'word'|'punctuation'} type
 * @property {string} value
 * @property {number} confidence
 * @property {Duration} position
 * @property {Duration} duration
 * @property {LanguageCode} [language]
 */

/**
 * @typedef {'lexical'|'normalized'} TranscriptInterpretationType
 */

/**
 * @typedef {Object} TranscriptInterpretation
 * @property {TranscriptInterpretationType} type
 * @property {string} transcript
 * @property {TranscriptToken[]} [tokens]
 */

/**
 * @typedef {Object} TranscriptAlternative
 * @property {number} confidence
 * @property {LanguageCode[]} [languages]
 * @property {TranscriptInterpretation[]} interpretations
 */

/**
 * @typedef {Object} EventEntityDataTranscript
 * @property {Uuid} id
 * @property {MediaChannel} channel
 * @property {boolean} isFinal
 * @property {Duration} [position]
 * @property {Duration} [duration]
 * @property {TranscriptAlternative[]} alternatives
 */

/**
 * @typedef {EventEntityBase<'transcript', EventEntityDataTranscript>} EventEntityTranscript
 */
