package nqt.base_java_spring_be.config.websocket;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.socket.config.annotation.EnableWebSocketMessageBroker;
import org.springframework.web.socket.config.annotation.StompEndpointRegistry;
import org.springframework.web.socket.config.annotation.WebSocketMessageBrokerConfigurer;

import java.util.Arrays;

@Configuration
@EnableWebSocketMessageBroker
public class WebSocketCommonConfig implements WebSocketMessageBrokerConfigurer {

    @Value("${app.websocket.endpoint:/ws}")
    private String websocketEndpoint;

    @Value("${app.websocket.allowed-origins:http://localhost:8686,http://192.165.92.202:3000,https://*.tthhospital.vn}")
    private String allowedOrigins;

    @Override
    public void registerStompEndpoints(StompEndpointRegistry registry) {
        String[] origins = Arrays.stream(allowedOrigins.split(","))
                .map(String::trim).filter(s -> !s.isEmpty()).toArray(String[]::new);

        registry.addEndpoint(websocketEndpoint)
                .setAllowedOriginPatterns(origins)
                .withSockJS(); // FE của bạn đang dùng SockJS
    }
    // Không override configureMessageBroker ở đây
}