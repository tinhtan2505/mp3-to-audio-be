package nqt.base_java_spring_be.config.websocket;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.lang.NonNull;
import org.springframework.messaging.Message;
import org.springframework.messaging.simp.stomp.StompCommand;
import org.springframework.messaging.simp.stomp.StompHeaderAccessor;
import org.springframework.messaging.support.MessageBuilder;
import org.springframework.messaging.support.MessageHeaderAccessor;
import org.springframework.web.socket.messaging.StompSubProtocolErrorHandler;

import java.nio.charset.StandardCharsets;

@Configuration
public class StompErrorHandlerConfig {

    // Đặt name = "subProtocolErrorHandler" để Spring tự động dùng bean này
    @Bean(name = "subProtocolErrorHandler")
    public StompSubProtocolErrorHandler stompErrorHandler() {
        return new StompSubProtocolErrorHandler() {

            @Override
            public @NonNull Message<byte[]> handleClientMessageProcessingError(@NonNull Message<byte[]> clientMessage, @NonNull Throwable ex) {
                // Tạo ERROR frame
                StompHeaderAccessor accessor = StompHeaderAccessor.create(StompCommand.ERROR);
                accessor.setMessage(ex.getMessage() != null ? ex.getMessage() : "Internal error");
                accessor.setLeaveMutable(true);

                // Giữ lại sessionId nếu có
                StompHeaderAccessor in = MessageHeaderAccessor.getAccessor(clientMessage, StompHeaderAccessor.class);
                accessor.setSessionId(in.getSessionId());

                byte[] payload = ("STOMP error: " +
                        (ex.getMessage() != null ? ex.getMessage() : "Unknown"))
                        .getBytes(StandardCharsets.UTF_8);

                return MessageBuilder.createMessage(payload, accessor.getMessageHeaders());
            }

            @Override
            public @NonNull Message<byte[]> handleErrorMessageToClient(@NonNull Message<byte[]> errorMessage) {
                // Có thể tuỳ biến thêm ở đây hoặc dùng mặc định
                return super.handleErrorMessageToClient(errorMessage);
            }
        };
    }
}
