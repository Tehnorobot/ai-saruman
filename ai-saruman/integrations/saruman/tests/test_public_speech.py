"""One utterance in both transports; never issue game commands."""
import unittest
from saruman.public_speech import chat_parts,queue_public_speech
from saruman.mod_goals import public_chat_text


class PublicSpeechTests(unittest.TestCase):
    def test_same_normalized_text_goes_to_chat_and_voice(self):
        ctx={'chat_queue':[],'tts_queue':[]}
        text=queue_public_speech(ctx,'First sentence.\nSecond sentence.','neutral')
        self.assertEqual(text,'First sentence. Second sentence.')
        self.assertEqual(''.join(ctx['chat_queue']),ctx['tts_queue'][0]['text'])

    def test_long_reply_is_not_summarized_or_truncated_in_chat(self):
        text=('A whole story with every word intact. '*18).strip()
        parts=chat_parts(text)
        self.assertGreater(len(parts),1);self.assertEqual(''.join(parts),text)
        for part in parts:self.assertLessEqual(len(part.encode('utf-16-le'))//2,240)

    def test_utf16_budget_handles_emoji_and_a_long_unbroken_word(self):
        text='word '+('🙂'*300)+' end'
        parts=chat_parts(text)
        self.assertEqual(''.join(parts),text)
        for part in parts:public_chat_text(part.strip())

    def test_transport_split_never_turns_quoted_syntax_into_commands(self):
        for prefix in ('@','/','$','!'):
            text=('x'*239)+' '+prefix+'quoted syntax in an ordinary sentence'
            parts=chat_parts(text)
            self.assertEqual(''.join(parts),text)
            self.assertTrue(all(not p.strip().startswith(('@','/','$','!')) for p in parts))

    def test_invalid_command_line_is_rejected_before_either_queue_is_modified(self):
        ctx={'chat_queue':[],'tts_queue':[]}
        with self.assertRaises(ValueError):queue_public_speech(ctx,'Hello\n@follow someone','neutral')
        self.assertEqual(ctx,{'chat_queue':[],'tts_queue':[]})

    def test_direct_reply_is_not_silently_dropped_when_three_voices_are_queued(self):
        ctx={'chat_queue':[],'tts_queue':[{'text':'old'} for _ in range(3)]}
        queue_public_speech(ctx,'A requested answer.','neutral')
        self.assertEqual(ctx['tts_queue'][-1]['text'],ctx['chat_queue'][-1])
        self.assertEqual(len(ctx['tts_queue']),4)


if __name__=='__main__':unittest.main()
