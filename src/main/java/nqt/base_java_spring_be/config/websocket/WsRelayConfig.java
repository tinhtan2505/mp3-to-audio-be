package nqt.base_java_spring_be.config.websocket;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Configuration;
import org.springframework.messaging.simp.config.MessageBrokerRegistry;
import org.springframework.web.socket.config.annotation.WebSocketMessageBrokerConfigurer;

import java.util.Arrays;

@Configuration
@ConditionalOnProperty(name = "app.ws.useRelay", havingValue = "true")
public class WsRelayConfig implements WebSocketMessageBrokerConfigurer {

    @Value("${app.websocket.application-destination-prefix:/app}")
    private String appDestinationPrefix;

    @Value("${app.websocket.broker-prefixes:/topic,/queue}")
    private String brokerPrefixes;

    @Value("${spring.rabbitmq.host:localhost}")
    private String rabbitHost;

    @Value("${rabbitmq.stomp.port:61613}")
    private Integer stompPort;

    @Value("${spring.rabbitmq.username:guest}")
    private String rabbitUser;

    @Value("${spring.rabbitmq.password:guest}")
    private String rabbitPass;

    @Value("${app.ws.auto-startup:true}")
    private boolean autoStartup;

    @Override
    public void configureMessageBroker(MessageBrokerRegistry registry) {
        String[] prefixes = Arrays.stream(brokerPrefixes.split(","))
                .map(String::trim).filter(s -> !s.isEmpty()).toArray(String[]::new);

        var relay = registry.enableStompBrokerRelay(prefixes)
                .setRelayHost(rabbitHost)
                .setRelayPort(stompPort)
                .setClientLogin(rabbitUser)
                .setClientPasscode(rabbitPass)
                .setSystemLogin(rabbitUser)
                .setSystemPasscode(rabbitPass);

        if (!autoStartup) {
            relay.setAutoStartup(false); // tránh app fail nếu broker tạm chưa sẵn sàng
        }

        registry.setApplicationDestinationPrefixes(appDestinationPrefix);
        registry.setUserDestinationPrefix("/user");
    }
}
