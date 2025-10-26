package nqt.base_java_spring_be.exception;

import jakarta.validation.ConstraintViolationException;
import lombok.extern.slf4j.Slf4j;
import nqt.base_java_spring_be.dto.CustomResponse;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.*;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.validation.FieldError;
import org.springframework.web.HttpMediaTypeNotSupportedException;
import org.springframework.web.HttpRequestMethodNotSupportedException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.resource.NoResourceFoundException;

import java.util.HashMap;
import java.util.Map;
import java.util.stream.Collectors;

@RestControllerAdvice
@Slf4j
public class GlobalExceptionHandler {

    // 404 cho "đường dẫn không có handler"
    @ExceptionHandler(NoResourceFoundException.class)
    public ResponseEntity<CustomResponse<Void>> handleNoResource(NoResourceFoundException ex) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(CustomResponse.error("Không tìm thấy endpoint", HttpStatus.NOT_FOUND.value()));
    }

    // 400 cho dữ liệu body không hợp lệ (@Valid)
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<CustomResponse<Void>> handleMethodArgumentNotValid(MethodArgumentNotValidException ex) {
        Map<String, String> errors = ex.getBindingResult()
                .getFieldErrors()
                .stream()
                .collect(Collectors.toMap(
                        FieldError::getField,
                        fe -> fe.getDefaultMessage() == null ? "invalid" : fe.getDefaultMessage(),
                        (a, b) -> a
                ));
        var metadata = new HashMap<String, Object>();
        metadata.put("errors", errors);

        return ResponseEntity.badRequest()
                .body(new CustomResponse<>("Dữ liệu không hợp lệ", null, metadata));
    }

    // 400 cho @RequestParam/@PathVariable vi phạm constraint
    @ExceptionHandler(ConstraintViolationException.class)
    public ResponseEntity<CustomResponse<Void>> handleConstraintViolation(ConstraintViolationException ex) {
        var metadata = Map.of("errors", ex.getConstraintViolations()
                .stream().map(v -> v.getPropertyPath() + ": " + v.getMessage()).toList());
        return ResponseEntity.badRequest()
                .body(new CustomResponse<>("Tham số không hợp lệ", null, metadata));
    }

    // 400: type mismatch & JSON parse error
    @ExceptionHandler({MethodArgumentTypeMismatchException.class, HttpMessageNotReadableException.class})
    public ResponseEntity<CustomResponse<Void>> handleBadRequestParse(Exception ex) {
        return ResponseEntity.badRequest()
                .body(CustomResponse.error("Yêu cầu không hợp lệ", HttpStatus.BAD_REQUEST.value()));
    }

    // 400: thiếu request param
    @ExceptionHandler(MissingServletRequestParameterException.class)
    public ResponseEntity<CustomResponse<Void>> handleMissingParam(MissingServletRequestParameterException ex) {
        var metadata = Map.of("missingParam", ex.getParameterName());
        return ResponseEntity.badRequest()
                .body(new CustomResponse<>("Thiếu tham số bắt buộc", null, metadata));
    }

    // 405: method không hỗ trợ
    @ExceptionHandler(HttpRequestMethodNotSupportedException.class)
    public ResponseEntity<CustomResponse<Void>> handleMethodNotSupported(HttpRequestMethodNotSupportedException ex) {
        return ResponseEntity.status(HttpStatus.METHOD_NOT_ALLOWED)
                .body(CustomResponse.error("Phương thức không được hỗ trợ", HttpStatus.METHOD_NOT_ALLOWED.value()));
    }

    // 415: media type không hỗ trợ
    @ExceptionHandler(HttpMediaTypeNotSupportedException.class)
    public ResponseEntity<CustomResponse<Void>> handleMediaType(HttpMediaTypeNotSupportedException ex) {
        return ResponseEntity.status(HttpStatus.UNSUPPORTED_MEDIA_TYPE)
                .body(CustomResponse.error("Định dạng nội dung không được hỗ trợ", HttpStatus.UNSUPPORTED_MEDIA_TYPE.value()));
    }

    // 409: vi phạm ràng buộc DB
    @ExceptionHandler(DataIntegrityViolationException.class)
    public ResponseEntity<CustomResponse<Void>> handleDataIntegrity(DataIntegrityViolationException ex) {
        return ResponseEntity.status(HttpStatus.CONFLICT)
                .body(CustomResponse.error("Dữ liệu xung đột hoặc không hợp lệ", HttpStatus.CONFLICT.value()));
    }

    // 500: lỗi không xác định
    @ExceptionHandler(Exception.class)
    public ResponseEntity<CustomResponse<Void>> handleUnknown(Exception ex) {
        // log.error("Unhandled exception", ex);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(CustomResponse.error("Có lỗi xảy ra, vui lòng thử lại!", HttpStatus.INTERNAL_SERVER_ERROR.value()));
    }

    @ExceptionHandler(BadRequestException.class)
    public ResponseEntity<CustomResponse<Void>> handleBadRequest(BadRequestException ex) {
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .body(CustomResponse.error(ex.getMessage(), HttpStatus.BAD_REQUEST.value()));
    }

}
