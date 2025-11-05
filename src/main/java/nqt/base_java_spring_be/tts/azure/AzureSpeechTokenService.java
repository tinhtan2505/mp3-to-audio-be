package nqt.base_java_spring_be.tts.azure;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Instant;
import java.util.concurrent.atomic.AtomicReference;

@Service
public class AzureSpeechTokenService {

    private final String key;
    private final String region;

    public AzureSpeechTokenService(
            @Value("${azure.speech.key}") String key,
            @Value("${azure.speech.region}") String region) {
        this.key = key;
        this.region = region;
    }

    private final HttpClient http = HttpClient.newHttpClient();
    private final AtomicReference<CachedToken> cached = new AtomicReference<>();

    public String getBearerToken() {
        var now = Instant.now();
        var c = cached.get();
        if (c != null && now.isBefore(c.expiresAt)) {
            return c.token;
        }
        try {
            // issue token (valid ~10 minutes)
            var req = HttpRequest.newBuilder()
                    .uri(URI.create("https://" + region + ".api.cognitive.microsoft.com/sts/v1.0/issueToken"))
                    .header("Ocp-Apim-Subscription-Key", key)
                    .POST(HttpRequest.BodyPublishers.noBody())
                    .build();
            var resp = http.send(req, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() / 100 != 2) {
                throw new RuntimeException("Failed to get Azure token: " + resp.statusCode() + " " + resp.body());
            }
            var token = resp.body();
            // token ~10 phút, mình gia hạn còn 9 phút để an toàn
            cached.set(new CachedToken(token, now.plusSeconds(9 * 60)));
            return token;
        } catch (Exception e) {
            throw new RuntimeException("Azure token error", e);
        }
    }

    private record CachedToken(String token, Instant expiresAt) {}
}
