package nqt.base_java_spring_be.realtime;

import lombok.RequiredArgsConstructor;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Component;

@Component
@RequiredArgsConstructor
public class RealtimeEvents {

    private final ApplicationEventPublisher publisher;

    public <T> void emitCreated(String entity, String id, T data, String actor) {
        publisher.publishEvent(CrudEvent.<T>builder()
                .action(CrudEvent.Action.CREATED)
                .entity(entity).id(id).data(data).actor(actor).ts(System.currentTimeMillis())
                .build());
    }

    public <T> void emitUpdated(String entity, String id, T data, String actor) {
        publisher.publishEvent(CrudEvent.<T>builder()
                .action(CrudEvent.Action.UPDATED)
                .entity(entity).id(id).data(data).actor(actor).ts(System.currentTimeMillis())
                .build());
    }

    public void emitDeleted(String entity, String id, String actor) {
        publisher.publishEvent(CrudEvent.<Object>builder()
                .action(CrudEvent.Action.DELETED)
                .entity(entity).id(id).data(null).actor(actor).ts(System.currentTimeMillis())
                .build());
    }
}
