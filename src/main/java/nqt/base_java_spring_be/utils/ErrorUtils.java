package nqt.base_java_spring_be.utils;

import org.springframework.http.HttpStatus;

import java.nio.file.AccessDeniedException;


public class ErrorUtils {
    public static HttpStatus determineHttpStatus(Exception e) {
        if (e instanceof IllegalArgumentException) {
            return HttpStatus.BAD_REQUEST;
        }
//        else if (e instanceof ResourceNotFoundException) { // Giả sử bạn có một ngoại lệ tùy chỉnh cho tài nguyên không tìm thấy
//            return HttpStatus.NOT_FOUND;
//        }
        else if (e instanceof AccessDeniedException) { // Giả sử bạn có một ngoại lệ cho quyền truy cập
            return HttpStatus.FORBIDDEN;
        }
        // Thêm các loại ngoại lệ khác mà bạn muốn xử lý
        return HttpStatus.INTERNAL_SERVER_ERROR; // Mặc định trả về INTERNAL_SERVER_ERROR
    }
}
