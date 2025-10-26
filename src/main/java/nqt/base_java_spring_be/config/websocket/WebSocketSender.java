package nqt.base_java_spring_be.config.websocket;

import lombok.RequiredArgsConstructor;
import org.springframework.messaging.simp.SimpMessagingTemplate;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class WebSocketSender {

    private final SimpMessagingTemplate messagingTemplate;

    private static boolean isBrokerDest(String dest) {
        return dest != null && (dest.startsWith("/topic/") || dest.startsWith("/queue/"));
    }


    /**
     * Gửi message chung đến 1 topic/queue
     */
    public void send(String destination, Object payload) {
        if (!isBrokerDest(destination)) {
            // Chặn nhầm lẫn kiểu "/projects/..." gây Invalid destination
            throw new IllegalArgumentException("Destination must start with /topic or /queue: " + destination);
        }
        messagingTemplate.convertAndSend(destination, payload);
    }

    /**
     * Gửi message đến 1 user cụ thể (Spring sẽ map /user/{userId}/queue/...)
     */
    public void sendToUser(String userId, String destination, Object payload) {
        if (destination == null || !destination.startsWith("/queue/")) {
            // Ép về /queue nếu dev truyền sai
            if (destination == null || destination.isBlank()) {
                destination = "/queue/notifications";
            } else if (!destination.startsWith("/")) {
                destination = "/queue/" + destination;
            } else {
                // ví dụ dev lỡ truyền "/projects/xxx" -> chuyển thành "/queue/projects/xxx"
                destination = destination.replaceFirst("^/", "/queue/");
            }
        }
        messagingTemplate.convertAndSendToUser(userId, destination, payload);
    }
}
