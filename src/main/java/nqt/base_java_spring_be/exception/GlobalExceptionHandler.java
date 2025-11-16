package nqt.base_java_spring_be.exception;

import jakarta.validation.ConstraintViolationException;
import lombok.extern.slf4j.Slf4j;
import nqt.base_java_spring_be.dto.CustomResponse;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.validation.FieldError;
import org.springframework.web.HttpMediaTypeNotSupportedException;
import org.springframework.web.HttpRequestMethodNotSupportedException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.web.servlet.resource.NoResourceFoundException;

import java.util.Map;
import java.util.stream.Collectors;

@RestControllerAdvice
@Slf4j
public class GlobalExceptionHandler {

    // 404 cho "đường dẫn không có handler"
    @ExceptionHandler(NoResourceFoundException.class)
    public ResponseEntity<CustomResponse<Void>> handleNoResource(NoResourceFoundException ex) {
        CustomResponse<Void> body = CustomResponse.error("Không tìm thấy endpoint", HttpStatus.NOT_FOUND);
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(body);
    }

    // 400 cho dữ liệu body không hợp lệ (@Valid)
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<CustomResponse<Void>> handleMethodArgumentNotValid(MethodArgumentNotValidException ex) {
        Map<String, String> errors = ex.getBindingResult()
                .getFieldErrors()
                .stream()
                .collect(Collectors.toMap(
                        FieldError::getField,
                        fe -> fe.getDefaultMessage() != null ? fe.getDefaultMessage() : "invalid",
                        (a, b) -> a
                ));

        CustomResponse<Map<String, String>> responseBody = new CustomResponse<>(
                "Dữ liệu không hợp lệ",
                HttpStatus.BAD_REQUEST.value(),
                errors
        );

        var metadata = Map.of("errors", errors);
        return ResponseEntity.badRequest()
                .body(new CustomResponse<Void>("Dữ liệu không hợp lệ", HttpStatus.BAD_REQUEST.value(), null));
    }

    // 400 cho @RequestParam/@PathVariable vi phạm constraint
    @ExceptionHandler(ConstraintViolationException.class)
    public ResponseEntity<CustomResponse<Void>> handleConstraintViolation(ConstraintViolationException ex) {
        var metadata = Map.of("errors", ex.getConstraintViolations()
                .stream().map(v -> v.getPropertyPath() + ": " + v.getMessage()).toList());
        return ResponseEntity.badRequest()
                .body(new CustomResponse<Void>("Tham số không hợp lệ", HttpStatus.BAD_REQUEST.value(), null));
    }

    // 400: type mismatch & JSON parse error
    @ExceptionHandler({MethodArgumentTypeMismatchException.class, HttpMessageNotReadableException.class})
    public ResponseEntity<CustomResponse<Void>> handleBadRequestParse(Exception ex) {
        return ResponseEntity.badRequest()
                .body(CustomResponse.error("Yêu cầu không hợp lệ", HttpStatus.BAD_REQUEST));
    }

    // 400: thiếu request param
    @ExceptionHandler(MissingServletRequestParameterException.class)
    public ResponseEntity<CustomResponse<Void>> handleMissingParam(MissingServletRequestParameterException ex) {
        var metadata = Map.of("missingParam", ex.getParameterName());
        return ResponseEntity.badRequest()
                .body(new CustomResponse<Void>("Thiếu tham số bắt buộc", HttpStatus.BAD_REQUEST.value(), null));
    }

    // 405: method không hỗ trợ
    @ExceptionHandler(HttpRequestMethodNotSupportedException.class)
    public ResponseEntity<CustomResponse<Void>> handleMethodNotSupported(HttpRequestMethodNotSupportedException ex) {
        CustomResponse<Void> body = CustomResponse.error("Phương thức không được hỗ trợ", HttpStatus.METHOD_NOT_ALLOWED);
        return ResponseEntity.status(HttpStatus.METHOD_NOT_ALLOWED).body(body);
    }

    // 415: media type không hỗ trợ
    @ExceptionHandler(HttpMediaTypeNotSupportedException.class)
    public ResponseEntity<CustomResponse<Void>> handleMediaType(HttpMediaTypeNotSupportedException ex) {
        CustomResponse<Void> body = CustomResponse.error("Định dạng nội dung không được hỗ trợ", HttpStatus.UNSUPPORTED_MEDIA_TYPE);
        return ResponseEntity.status(HttpStatus.UNSUPPORTED_MEDIA_TYPE).body(body);
    }

    // 409: vi phạm ràng buộc DB
    @ExceptionHandler(DataIntegrityViolationException.class)
    public ResponseEntity<CustomResponse<Void>> handleDataIntegrity(DataIntegrityViolationException ex) {
        CustomResponse<Void> body = CustomResponse.error("Dữ liệu xung đột hoặc không hợp lệ", HttpStatus.CONFLICT);
        return ResponseEntity.status(HttpStatus.CONFLICT).body(body);
    }

    // 400: Business logic exception
    @ExceptionHandler(BadRequestException.class)
    public ResponseEntity<CustomResponse<Void>> handleBadRequest(BadRequestException ex) {
        CustomResponse<Void> body = CustomResponse.error(ex.getMessage(), HttpStatus.BAD_REQUEST);
        return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(body);
    }

    // 500: lỗi không xác định
    @ExceptionHandler(Exception.class)
    public ResponseEntity<CustomResponse<Void>> handleUnknown(Exception ex) {
        log.error("Unhandled exception", ex);
        CustomResponse<Void> body = CustomResponse.error("Có lỗi xảy ra, vui lòng thử lại!", HttpStatus.INTERNAL_SERVER_ERROR);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(body);
    }
}