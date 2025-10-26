package nqt.base_java_spring_be.realtime;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import nqt.base_java_spring_be.config.websocket.WebSocketSender;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionPhase;
import org.springframework.transaction.event.TransactionalEventListener;

@Slf4j
@Component
@RequiredArgsConstructor
public class CrudEventWsListener {

    private final WebSocketSender ws;

    // Topic chuẩn cho Project
    private static final String PROJECT_TOPIC = "/topic/projects";

    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
    public void onCrudEvent(CrudEvent<?> e) {
        // Gửi 2 kênh: 1) danh sách  2) chi tiết theo id (tuỳ FE có subscribe hay không)
        var envelope = new WsEnvelope<>(e); // gói chung (xem class bên dưới)
        ws.send(PROJECT_TOPIC, envelope);                 // ví dụ: list view
        ws.send(PROJECT_TOPIC + "/" + e.getId(), envelope); // ví dụ: chi tiết 1 item

        log.info("WS emitted: {}/{} -> action={}, id={}", e.getEntity(), PROJECT_TOPIC, e.getAction(), e.getId());
    }

    /** Gói bọc payload để FE dễ route theo type */
    public record WsEnvelope<T>(CrudEvent<T> event) {}
}
