package nqt.base_java_spring_be.config.websocket;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import nqt.base_java_spring_be.authentication.dto.UserPrincipal;
import nqt.base_java_spring_be.security.JwtTokenProvider;
import org.springframework.lang.NonNull;
import org.springframework.lang.Nullable;
import org.springframework.messaging.Message;
import org.springframework.messaging.MessageChannel;
import org.springframework.messaging.simp.stomp.StompCommand;
import org.springframework.messaging.simp.stomp.StompHeaderAccessor;
import org.springframework.messaging.support.ChannelInterceptor;
import org.springframework.messaging.support.MessageHeaderAccessor;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.authority.AuthorityUtils;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;

import java.nio.charset.StandardCharsets;
import java.security.Principal;
import java.util.List;
import java.util.UUID;

@Slf4j
@Component
@RequiredArgsConstructor
public class StompLoggingInterceptor implements ChannelInterceptor {

    private final JwtTokenProvider tokenProvider; // bạn đã có JwtTokenProvider

    @Override
    public Message<?> preSend(@NonNull Message<?> message, @NonNull MessageChannel channel) {
        StompHeaderAccessor h = MessageHeaderAccessor.getAccessor(message, StompHeaderAccessor.class);
        if (h == null) return message;

        StompCommand cmd = h.getCommand();
        String sid = h.getSessionId();
        Principal user = h.getUser();
        String dest = h.getDestination();
        String nativeAuthz = firstNative(h, "Authorization");
        String nativeToken = firstNative(h, "token"); // hỗ trợ header 'token' nếu FE gửi thế

        switch (cmd) {
            case CONNECT -> {
                // 1) Ưu tiên SecurityContext nếu đã có (đã auth ở layer HTTP handshake)
                Authentication ctxAuth = SecurityContextHolder.getContext().getAuthentication();
                if (ctxAuth == null || !ctxAuth.isAuthenticated()) {
                    // 2) Nếu chưa có, thử lấy token từ native headers của STOMP CONNECT
                    String token = extractBearer(nativeAuthz);
                    if (!StringUtils.hasText(token)) token = nativeToken;

                    if (StringUtils.hasText(token) && tokenProvider.validateToken(token)) {
                        String username = tokenProvider.getUsernameFromToken(token);
                        UUID uid = tokenProvider.getUserIdFromToken(token); // bạn đã support claim uid
                        UserPrincipal principal = new UserPrincipal(uid, username);

                        UsernamePasswordAuthenticationToken auth =
                                new UsernamePasswordAuthenticationToken(principal, null,
                                        AuthorityUtils.NO_AUTHORITIES);
                        h.setUser(auth); // set Principal cho phiên STOMP
                        SecurityContextHolder.getContext().setAuthentication(auth);
                        log.info("STOMP CONNECT ok sid={} user={} accept-version={} heart-beat={} host={}",
                                sid, username,
                                firstNative(h, "accept-version"),
                                firstNative(h, "heart-beat"),
                                firstNative(h, "host"));
                    } else {
                        log.warn("STOMP CONNECT reject sid={} reason=invalid_or_missing_token", sid);
                        throw new IllegalArgumentException("Invalid or missing token");
                    }
                } else {
                    log.info("STOMP CONNECT ok sid={} user={} (from SecurityContext)", sid, ctxAuth.getName());
                    h.setUser(ctxAuth);
                }
            }
            case SUBSCRIBE -> {
                String subId = h.getSubscriptionId();
                log.info("STOMP SUBSCRIBE sid={} user={} subId={} dest={}",
                        sid, name(user), subId, dest);
            }
            case UNSUBSCRIBE -> {
                String subId = h.getSubscriptionId();
                log.info("STOMP UNSUBSCRIBE sid={} user={} subId={}", sid, name(user), subId);
            }
            case SEND -> {
                int len = payloadSize(message.getPayload());
                // Tránh log full payload (có thể chứa PII); chỉ log kích thước + dest
                log.info("STOMP SEND sid={} user={} dest={} payloadSize={}",
                        sid, name(user), dest, len);
            }
            case DISCONNECT -> {
                log.info("STOMP DISCONNECT sid={} user={}", sid, name(user));
                // Optionally clear SecurityContext khi ngắt
                SecurityContextHolder.clearContext();
            }
            default -> { /* NOP for MESSAGE, RECEIPT, ERROR, HEARTBEAT */ }
        }
        return message;
    }

    @Nullable
    private String firstNative(StompHeaderAccessor h, String key) {
        List<String> vals = h.getNativeHeader(key);
        return vals.isEmpty() ? null : vals.get(0);
    }

    private String extractBearer(@Nullable String authz) {
        if (!StringUtils.hasText(authz)) return null;
        if (authz.startsWith("Bearer ")) return authz.substring(7);
        return authz;
    }

    private int payloadSize(Object payload) {
        if (payload == null) return 0;
        if (payload instanceof byte[] b) return b.length;
        if (payload instanceof String s) return s.getBytes(StandardCharsets.UTF_8).length;
        return payload.toString().getBytes(StandardCharsets.UTF_8).length;
    }

    private String name(Principal p) {
        return p == null ? "anonymous" : p.getName();
    }
}
