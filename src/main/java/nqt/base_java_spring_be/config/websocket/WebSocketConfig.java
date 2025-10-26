package nqt.base_java_spring_be.config.websocket;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.messaging.simp.config.MessageBrokerRegistry;
import org.springframework.web.socket.config.annotation.EnableWebSocketMessageBroker;
import org.springframework.web.socket.config.annotation.WebSocketMessageBrokerConfigurer;
import org.springframework.web.socket.config.annotation.StompEndpointRegistry;

@Configuration
@EnableWebSocketMessageBroker  // Kích hoạt WebSocket server với STOMP
public class WebSocketConfig implements WebSocketMessageBrokerConfigurer {

    // Đọc các cấu hình từ application.properties
    @Value("${app.ws.useRelay:false}") private boolean useRelay;
    @Value("${app.ws.auto-startup:true}") private boolean autoStartup;
    @Value("${stomp.virtualHost:/}") private String virtualHost;

    @Value("${spring.rabbitmq.host}")   private String rabbitHost;
    @Value("${spring.rabbitmq.username}") private String rabbitUser;
    @Value("${spring.rabbitmq.password}") private String rabbitPass;
    @Value("${rabbitmq.stomp.port}")   private Integer stompPort;
    @Value("${app.websocket.endpoint}") private String websocketEndpoint;
    @Value("${app.websocket.application-destination-prefix}") private String appDestinationPrefix;
    @Value("${app.websocket.broker-prefixes}") private String brokerPrefixes;

    @Override
    public void registerStompEndpoints(StompEndpointRegistry registry) {
        // Khai báo endpoint WebSocket cho client kết nối
        registry.addEndpoint(websocketEndpoint)
                .setAllowedOriginPatterns("*")   // Cho phép mọi nguồn (có thể cấu hình domain cụ thể)
                .withSockJS();                  // Kích hoạt SockJS fallback (hỗ trợ các trình duyệt không hỗ trợ WebSocket)
    }

    @Override
    public void configureMessageBroker(MessageBrokerRegistry registry) {
        registry.setApplicationDestinationPrefixes(appDestinationPrefix);

        if (useRelay) {
            String[] relayPrefixes = brokerPrefixes.split("\\s*,\\s*");
            registry.enableStompBrokerRelay(relayPrefixes)
                    .setRelayHost(rabbitHost)
                    .setRelayPort(stompPort)
                    .setVirtualHost(virtualHost)
                    .setSystemLogin(rabbitUser)
                    .setSystemPasscode(rabbitPass)
                    .setClientLogin(rabbitUser)
                    .setClientPasscode(rabbitPass)
                    .setAutoStartup(autoStartup)
                    // đặt heartbeat để giữ kết nối khỏe + phát hiện sớm đứt kết nối
                    .setSystemHeartbeatSendInterval(10_000)
                    .setSystemHeartbeatReceiveInterval(10_000);
        } else {
            registry.enableSimpleBroker("/topic", "/queue");
        }

        // (Không bắt buộc) đặt rõ user destination prefix (mặc định là "/user")
        registry.setUserDestinationPrefix("/user");
    }
}
