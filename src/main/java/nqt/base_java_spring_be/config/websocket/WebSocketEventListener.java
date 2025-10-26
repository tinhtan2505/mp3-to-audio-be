package nqt.base_java_spring_be.config.websocket;

import lombok.extern.slf4j.Slf4j;
import org.springframework.context.event.EventListener;
import org.springframework.messaging.simp.stomp.StompHeaderAccessor;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.messaging.SessionConnectEvent;
import org.springframework.web.socket.messaging.SessionDisconnectEvent;

@Slf4j
@Component
public class WebSocketEventListener {

    @EventListener
    public void handleWebSocketConnectListener(SessionConnectEvent event) {
        StompHeaderAccessor accessor = StompHeaderAccessor.wrap(event.getMessage());
        accessor.getUser();
//        log.info("WS CONNECT: sessionId={}, user={}",
//                accessor.getSessionId(),
//                accessor.getUser().getName());
    }

    @EventListener
    public void handleWebSocketDisconnectListener(SessionDisconnectEvent event) {
        StompHeaderAccessor accessor = StompHeaderAccessor.wrap(event.getMessage());
        accessor.getUser();
//        log.info("WS DISCONNECT: sessionId={}, user={}, closeStatus={}",
//                accessor.getSessionId(),
//                accessor.getUser().getName(),
//                event.getCloseStatus());
    }
}