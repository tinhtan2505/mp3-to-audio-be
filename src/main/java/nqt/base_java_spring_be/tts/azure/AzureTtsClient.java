package nqt.base_java_spring_be.tts.azure;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;

@Component
public class AzureTtsClient {

    private final AzureSpeechTokenService tokenService;
    private final String region;
    private final String voice;
    private final String outputFormat;

    public AzureTtsClient(
            AzureSpeechTokenService tokenService,
            @Value("${azure.speech.region}") String region,
            @Value("${azure.speech.voice:vi-VN-HoaiMyNeural}") String voice,
            @Value("${azure.speech.format:audio-24khz-48kbitrate-mono-mp3}") String outputFormat) {
        this.tokenService = tokenService;
        this.region = region;
        this.voice = voice;
        this.outputFormat = outputFormat;
    }

    private final HttpClient http = HttpClient.newHttpClient();

    public byte[] synthesizeViMp3(String text) {
        try {
            String ssml = buildSsml(text, voice);
            String token = tokenService.getBearerToken();

            var req = HttpRequest.newBuilder()
                    .uri(URI.create("https://" + region + ".tts.speech.microsoft.com/cognitiveservices/v1"))
                    .header("Authorization", "Bearer " + token)
                    .header("Content-Type", "application/ssml+xml")
                    .header("X-Microsoft-OutputFormat", outputFormat)
                    .header("User-Agent", "spring-azure-tts")
                    .POST(HttpRequest.BodyPublishers.ofString(ssml))
                    .build();

            var resp = http.send(req, HttpResponse.BodyHandlers.ofByteArray());
            if (resp.statusCode() / 100 != 2) {
                throw new RuntimeException("Azure TTS failed: " + resp.statusCode() + " " + new String(resp.body()));
            }
            return resp.body();
        } catch (Exception e) {
            throw new RuntimeException("Azure TTS error", e);
        }
    }

    private String buildSsml(String text, String voiceName) {
        String escaped = escapeXml(text);
        // Bạn có thể tinh chỉnh rate/pitch để tạo "nhân vật"
        return """
                <speak version="1.0" xml:lang="vi-VN">
                  <voice name="%s">
                    <prosody rate="100%%">%s</prosody>
                  </voice>
                </speak>
                """.formatted(voiceName, escaped);
    }

    private String escapeXml(String s) {
        return s.replace("&","&amp;")
                .replace("<","&lt;")
                .replace(">","&gt;")
                .replace("\"","&quot;")
                .replace("'","&apos;");
    }
}
