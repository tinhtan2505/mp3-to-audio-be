package nqt.base_java_spring_be.security;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.NonNull;
import lombok.RequiredArgsConstructor;
import nqt.base_java_spring_be.authentication.dto.UserPrincipal;
import nqt.base_java_spring_be.entity.User;
import nqt.base_java_spring_be.repository.UserRepository;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.authentication.WebAuthenticationDetailsSource;
import org.springframework.util.StringUtils;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.UUID;

@RequiredArgsConstructor
public class JwtAuthenticationFilter extends OncePerRequestFilter {

    private final JwtTokenProvider tokenProvider;
    private final UserRepository userRepository; // để nạp user + id (nếu token không chứa uid)

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    @NonNull HttpServletResponse response,
                                    @NonNull FilterChain filterChain) throws ServletException, IOException {
        // 0) Nếu đã có Authentication thì bỏ qua
        if (SecurityContextHolder.getContext().getAuthentication() != null) {
            filterChain.doFilter(request, response);
            return;
        }

        String bearer = request.getHeader("Authorization");
        String token = (StringUtils.hasText(bearer) && bearer.startsWith("Bearer ")) ? bearer.substring(7) : null;

        // ★ NEW: lấy token từ query (?token=...) – hữu ích cho SockJS handshake /ws?token=...
        if (!StringUtils.hasText(token)) {
            token = request.getParameter("token");
        }

        // ★ NEW: (tuỳ chọn) lấy token từ cookie "access_token"
        if (!StringUtils.hasText(token) && request.getCookies() != null) {
            for (var c : request.getCookies()) {
                if ("access_token".equals(c.getName()) && StringUtils.hasText(c.getValue())) {
                    token = c.getValue();
                    break;
                }
            }
        }

        if (StringUtils.hasText(token) && tokenProvider.validateToken(token)) {
            String username = tokenProvider.getUsernameFromToken(token);

            // (A) Nếu bạn đưa uid vào JWT claim, lấy luôn:
            UUID uid = tokenProvider.getUserIdFromToken(token); // sẽ thêm ở bước 3
            UserPrincipal principal;

            if (uid != null) {
                principal = new UserPrincipal(uid, username);
            } else {
                // (B) Nếu token KHÔNG có uid: nạp DB để có id
                User user = userRepository.findByUsername(username).orElse(null);
                if (user != null) {
                    principal = new UserPrincipal(user.getId(), user.getUsername());
                } else {
                    filterChain.doFilter(request, response);
                    return;
                }
            }

            UsernamePasswordAuthenticationToken authentication =
                    new UsernamePasswordAuthenticationToken(principal, null, principal.getAuthorities());
            authentication.setDetails(new WebAuthenticationDetailsSource().buildDetails(request));
            SecurityContextHolder.getContext().setAuthentication(authentication);
        }

        filterChain.doFilter(request, response);
    }
}
