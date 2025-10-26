package nqt.base_java_spring_be.realtime;

import lombok.Builder;
import lombok.Value;

@Value
@Builder
public class CrudEvent<T> {
    public enum Action { CREATED, UPDATED, DELETED }

    Action action;
    String entity;
    String id;
    T data;
    String actor;
    long ts;
}
